"""Phase 3: pairwise portfolio comparison and the harness self-improvement loop."""
from __future__ import annotations

import copy
import datetime as dt
import itertools
import json
import statistics

from sqlalchemy.orm import Session

from .harness import schemas
from .harness.committee import eligible_labs
from .harness.defaults import current_harness, render
from .harness.runner import Engine, create_run
from .leaderboard import window_papers
from .llm import complete
from .models import Author, HarnessVersion, ImprovementReport, Paper, PortfolioComparison, ReviewRun, Review
from .models import utcnow


# ----------------------------------------------------------------------------- pairwise comparison

def _portfolio_text(papers: list[Paper]) -> str:
    out = []
    for p in papers:
        out.append(f"- {p.acr_id} · {p.title} ({p.paper_type}, {p.field}, priority {p.priority_at.date()})\n"
                   f"  creativity {p.score_creativity} [O {p.score_originality} D {p.score_depth} "
                   f"P {p.score_potential_impact} I {p.score_implementation}]\n  {p.abstract[:700]}")
    return "\n".join(out)


def run_pairwise_round(db: Session, window_days: int = 90, max_pairs: int = 20) -> int:
    hv = current_harness(db)
    end = utcnow()
    by_author: dict[int, list[Paper]] = {}
    for p in window_papers(db, window_days, end):
        by_author.setdefault(p.author_id, []).append(p)
    authors = [db.get(Author, i) for i in by_author]
    pairs = list(itertools.combinations(authors, 2))
    # prefer pairs not yet compared in this window recently
    recent = {(c.author_a_id, c.author_b_id) for c in db.query(PortfolioComparison)
              .filter(PortfolioComparison.window_days == window_days,
                      PortfolioComparison.created_at >= end - dt.timedelta(days=window_days)).all()}
    pairs = [(a, b) for a, b in pairs if (a.id, b.id) not in recent and (b.id, a.id) not in recent][:max_pairs]
    done = 0
    for k, (a, b) in enumerate(pairs):
        labs = eligible_labs(hv.config, a.lab, {b.lab})
        if not labs:
            continue
        judge = labs[(a.id + b.id + k) % len(labs)]
        # randomize presentation order to cancel position bias
        flip = (a.id + b.id) % 2 == 1
        A, B = (b, a) if flip else (a, b)
        user = render(hv.config["prompts"]["comparison"], window_days=window_days, name_a=A.name, name_b=B.name,
                      papers_a=_portfolio_text(by_author[A.id]), papers_b=_portfolio_text(by_author[B.id]))
        res = complete(judge, "You judge research portfolios for ACR.", [{"role": "user", "content": user}],
                       schemas.COMPARISON_SCHEMA, max_tokens=4000)
        out = res.json or {"preference": "tie", "confidence": 0.5, "rationale": "unparseable"}
        pref = out.get("preference", "tie")
        if flip and pref in ("a", "b"):
            pref = "b" if pref == "a" else "a"
        db.add(PortfolioComparison(window_days=window_days, window_end=end, author_a_id=a.id, author_b_id=b.id,
                                   judge_lab=judge["lab"], judge_model=judge["model"], harness_version=hv.version,
                                   papers_a=[p.acr_id for p in by_author[a.id]], papers_b=[p.acr_id for p in by_author[b.id]],
                                   preference=pref, confidence=float(out.get("confidence", 0.5)),
                                   rationale=out.get("rationale", ""),
                                   transcript={"prompt": user, "response": res.text, "presented_flipped": flip,
                                               "note": res.note}))
        db.flush()
        done += 1
    return done


# ----------------------------------------------------------------------------- self-improvement loop

def diagnostics(db: Session, hv: HarnessVersion, limit: int = 40) -> dict:
    runs = (db.query(ReviewRun).filter(ReviewRun.sandbox.is_(False), ReviewRun.stage == "done",
                                       ReviewRun.harness_version == hv.version)
            .order_by(ReviewRun.id.desc()).limit(limit).all())
    if not runs:
        return {"runs": 0, "note": "no completed official runs under this harness"}
    spreads, deltas, chair_div, accept = [], [], [], 0
    lab_bias: dict[str, list[float]] = {}
    for r in runs:
        finals = [rv.scores.get("creativity", 0) for rv in r.reviews if rv.scores]
        inits = [rv.initial.get("scores", {}).get("creativity", 0) for rv in r.reviews]
        if len(finals) >= 2:
            spreads.append(statistics.pstdev(finals))
        if finals and inits and r.rebuttal_text:
            deltas.append(statistics.mean(finals) - statistics.mean(inits))
        if finals:
            chair_div.append(r.final_scores.get("creativity", 0) - statistics.mean(finals))
        accept += r.decision == "accept"
        for rv in r.reviews:
            lab_bias.setdefault(rv.lab, []).append(rv.scores.get("creativity", 0))
    errors = db.query(ReviewRun).filter(ReviewRun.harness_version == hv.version, ReviewRun.stage == "failed").count()
    return {
        "runs": len(runs), "accept_rate": round(accept / len(runs), 3),
        "reviewer_creativity_spread_mean": round(statistics.mean(spreads), 2) if spreads else None,
        "rebuttal_effect_mean": round(statistics.mean(deltas), 2) if deltas else None,
        "chair_minus_reviewer_mean": round(statistics.mean(chair_div), 2) if chair_div else None,
        "per_lab_mean_creativity": {k: round(statistics.mean(v), 2) for k, v in lab_bias.items()},
        "failed_runs": errors,
        "prior_art_items_mean": round(statistics.mean(len(r.prior_art.get("items", [])) for r in runs), 1),
    }


def _set_path(cfg: dict, path: str, value):
    keys = path.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def next_draft_version(db: Session, base: str) -> str:
    major, minor, *_ = (base.split("-")[0].split(".") + ["0", "0"])
    n = 1
    while True:
        v = f"{major}.{int(minor) + 1}.0-draft.{n}"
        if not db.query(HarnessVersion).filter(HarnessVersion.version == v).first():
            return v
        n += 1


def run_improvement(db: Session, report_id: int, calibration_size: int = 3) -> None:
    rep = db.get(ImprovementReport, report_id)
    base = db.query(HarnessVersion).filter(HarnessVersion.version == rep.base_version).first()
    try:
        diag = diagnostics(db, base)
        rep.diagnostics = diag
        improver = base.config["committee"]["labs"][0]
        user = render(base.config["prompts"]["improver"], diagnostics=json.dumps(diag, indent=1),
                      config=json.dumps(base.config, indent=1)[:60000])
        res = complete(improver, "You maintain a peer-review harness.", [{"role": "user", "content": user}],
                       schemas.IMPROVER_SCHEMA, max_tokens=16000)
        proposal = res.json or {"diagnosis": "unparseable", "changes": [], "changelog": ""}
        rep.proposal = proposal
        cfg = copy.deepcopy(base.config)
        applied = []
        for ch in proposal.get("changes", []):
            path = ch.get("path", "")
            if path.split(".")[0] in ("prompts", "rubric", "limits", "prior_art") and isinstance(ch.get("new_value"), str):
                val = ch["new_value"]
                if path.startswith("rubric") or path.startswith("limits"):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass
                _set_path(cfg, path, val)
                applied.append(path)
        draft = HarnessVersion(version=next_draft_version(db, base.version), config=cfg, is_draft=True,
                               origin="self_improvement", parent_version=base.version,
                               changelog="[auto-proposed, unpublished] " + proposal.get("changelog", "") +
                                         f"\nApplied paths: {applied}\nDiagnosis: {proposal.get('diagnosis', '')}")
        db.add(draft)
        db.flush()
        rep.draft_version = draft.version
        # calibration: sandbox-review the most recent decided papers with the draft, compare to the record
        papers = (db.query(Paper).filter(Paper.official_run_id.isnot(None), Paper.score_creativity.isnot(None))
                  .order_by(Paper.decided_at.desc()).limit(calibration_size).all())
        cal = []
        for p in papers:
            run = create_run(db, p, draft, sandbox=True)
            eng = Engine(db, run)
            while run.stage not in ("done", "failed"):
                eng.advance()
            cal.append({"acr_id": p.acr_id, "run_id": run.id, "official_decision": p.decision,
                        "draft_decision": run.decision, "official_creativity": p.score_creativity,
                        "draft_creativity": run.final_scores.get("creativity")})
        agree = [c for c in cal if c["official_decision"] == c["draft_decision"]]
        rep.calibration = {"papers": cal, "decision_agreement": round(len(agree) / len(cal), 2) if cal else None,
                           "mean_abs_creativity_delta": round(statistics.mean(
                               abs((c["draft_creativity"] or 0) - (c["official_creativity"] or 0)) for c in cal), 2) if cal else None}
        rep.status = "ready"
    except Exception as e:
        rep.status = "failed"
        rep.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        rep.finished_at = utcnow()
        db.flush()
