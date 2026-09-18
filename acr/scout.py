"""Scout: scan a day's arXiv listing, triage every paper with the committee's models from title and abstract,
pick the most promising, ingest them as benchmark submissions, and keep the whole selection on record."""
from __future__ import annotations

import datetime as dt
import logging
import statistics
import time

from sqlalchemy.orm import Session

from .harness.defaults import current_harness
from .ingest_arxiv import fetch_listing_feed, ingest_entry, parse_feed, _get
from .llm import complete
from .models import Paper, ScoutRun
from .pricing import cost_usd

log = logging.getLogger("acr.scout")

AI_ML_CATEGORIES = {
    "cs.AI": "artificial intelligence", "cs.LG": "machine learning", "cs.CL": "computation and language",
    "cs.CV": "computer vision", "cs.NE": "neural and evolutionary computing", "cs.IR": "information retrieval",
    "cs.MA": "multiagent systems", "cs.RO": "robotics", "stat.ML": "machine learning (statistics)",
}

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "arxiv_id": {"type": "string"},
            "promise": {"type": "number", "minimum": 0, "maximum": 10,
                        "description": "How likely this is a genuinely creative contribution worth a full review: 0-10, use the whole scale, be sparing above 7"},
            "why": {"type": "string", "description": "One sentence: the idea and why it is (or is not) promising"},
        },
        "required": ["arxiv_id", "promise", "why"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}

TRIAGE_SYSTEM = """You are a scout for Agent Creativity Review. From title and abstract alone, estimate how promising each paper is as a creative contribution: a genuinely new idea, mechanism, or question, with real potential impact, that is developed enough to review. Score 0-10 and use the whole scale. Reserve 8+ for work that would surprise an expert. Score incremental benchmark tuning, minor architecture variants, surveys, position papers, and tooling reports low unless something about them is unusual. Do not reward hype, size, or the number of experiments. Judge each paper on its own; do not rank them against each other."""


def fetch_pool(categories: list[str]) -> list[dict]:
    """Today's new announcements whose primary category is in `categories`, deduplicated across cross-lists."""
    seen, pool = {}, []
    for cat in categories:
        try:
            entries = fetch_listing_feed(cat)
        except Exception as e:
            log.warning("listing %s failed: %s", cat, e)
            entries = []
        for e in entries:
            if e["primary_category"] in categories and e["arxiv_id"] not in seen:
                seen[e["arxiv_id"]] = True
                pool.append(e)
        time.sleep(1.0)
    return pool


def triage(pool: list[dict], scouts: list[dict], chunk: int = 25) -> tuple[dict[str, dict], float]:
    """Each scout model scores every paper; returns {arxiv_id: {scores{lab}, why{lab}}} and total cost."""
    results: dict[str, dict] = {e["arxiv_id"]: {"scores": {}, "why": {}} for e in pool}
    total = 0.0
    for spec in scouts:
        for i in range(0, len(pool), chunk):
            batch = pool[i:i + chunk]
            listing = "\n\n".join(f"[{e['arxiv_id']}] {e['title']}\nCategories: {', '.join(e['categories'])}\n{e['abstract'][:1800]}"
                                  for e in batch)
            user = f"Score each of the following {len(batch)} papers. Return one item per paper with its arxiv_id exactly as given.\n\n{listing}"
            try:
                r = complete(spec, TRIAGE_SYSTEM, [{"role": "user", "content": user}], TRIAGE_SCHEMA, max_tokens=16000)
            except Exception as e:
                log.warning("triage chunk failed for %s: %s", spec.get("lab"), e)
                continue
            if r.provider != "mock":
                total += cost_usd(spec.get("model", ""), r.input_tokens, r.output_tokens)
            for it in (r.json or {}).get("items", []):
                aid = str(it.get("arxiv_id", "")).strip().replace("arXiv:", "")
                if aid in results and isinstance(it.get("promise"), (int, float)):
                    results[aid]["scores"][spec["lab"]] = round(float(it["promise"]), 1)
                    results[aid]["why"][spec["lab"]] = str(it.get("why", ""))[:400]
    return results, round(total, 4)


def rank(pool: list[dict], results: dict[str, dict]) -> list[dict]:
    ranked = []
    for e in pool:
        res = results.get(e["arxiv_id"], {"scores": {}, "why": {}})
        scores = list(res["scores"].values())
        mean = round(statistics.mean(scores), 2) if scores else None
        ranked.append({"arxiv_id": e["arxiv_id"], "title": e["title"], "primary_category": e["primary_category"],
                       "url": e["url"], "scores": res["scores"], "why": res["why"], "mean": mean,
                       "spread": round(max(scores) - min(scores), 1) if len(scores) > 1 else 0.0})
    ranked.sort(key=lambda x: (-(x["mean"] if x["mean"] is not None else -1), -max(x["scores"].values(), default=0)))
    return ranked


def run_scout(db: Session, categories: dict[str, str] | None = None, max_picks: int = 20,
              scouts: list[dict] | None = None, min_mean: float = 0.0, ingest: bool = True) -> ScoutRun:
    categories = categories or AI_ML_CATEGORIES
    scouts = scouts or current_harness(db).config["committee"]["labs"]
    run = ScoutRun(listing_date=dt.date.today().isoformat(), categories=list(categories), max_picks=max_picks,
                   scouts=[{k: v for k, v in s.items() if k in ("lab", "provider", "model")} for s in scouts])
    db.add(run); db.flush(); db.commit()
    try:
        pool = fetch_pool(list(categories))
        run.pool_size = len(pool); db.commit()
        results, cost = triage(pool, scouts)
        ranked = rank(pool, results)
        run.candidates = ranked
        run.cost_usd = cost
        already = {p.external_id for p in db.query(Paper).filter(Paper.source_kind == "arxiv").all()}
        picks = [c for c in ranked if c["mean"] is not None and c["mean"] >= min_mean and c["arxiv_id"] not in already][:max_picks]
        by_id = {e["arxiv_id"]: e for e in pool}
        for c in picks:
            entry = by_id[c["arxiv_id"]]
            if ingest:
                paper = ingest_entry(db, entry, categories.get(entry["primary_category"], entry["primary_category"]))
                paper.scout_run_id = run.id
                c["acr_id"] = paper.acr_id
                db.commit()
        run.picks = picks
        run.status = "done"
    except Exception as e:
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        run.finished_at = dt.datetime.utcnow()
        db.commit()
    return run
