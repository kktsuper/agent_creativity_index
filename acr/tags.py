"""Topic tags: short free-form labels the chair writes for each paper ("llms", "reinforcement learning").

Deliberately not a category system: there is no approved vocabulary and no hierarchy. Normalization is light:
lowercase, tidy whitespace and punctuation, and reuse an existing tag's spelling when a new one differs from it
only by case, spacing, hyphens or a plural "s" ("LLMs" -> "llm" if "llm" is already in use).
"""
from __future__ import annotations

import re

MAX_TAGS = 5
MAX_TAG_CHARS = 40


def clean_tag(raw) -> str:
    """Lowercase, collapse whitespace, strip surrounding punctuation. Returns "" for anything unusable."""
    if not isinstance(raw, str):
        return ""
    t = re.sub(r"\s+", " ", raw).strip().lower()
    t = t.strip(" .,;:!?\"'`()[]{}#*")
    if not t or len(t) > MAX_TAG_CHARS or not re.search(r"[a-z0-9]", t):
        return ""
    return t


def tag_key(tag: str) -> str:
    """Comparison key for near-identical spellings: letters and digits only, one trailing plural "s" dropped."""
    k = re.sub(r"[^a-z0-9]", "", tag.lower())
    if len(k) > 3 and k.endswith("s") and not k.endswith("ss"):
        k = k[:-1]
    return k


def normalize_tags(raw_tags, existing: list[str] | None = None) -> list[str]:
    """Clean the model's tags, map each onto an existing spelling when one is near-identical, drop duplicates,
    keep at most MAX_TAGS in the model's order."""
    known: dict[str, str] = {}
    for t in existing or []:
        c = clean_tag(t)
        if c:
            known.setdefault(tag_key(c), c)
    out, seen = [], set()
    for raw in raw_tags if isinstance(raw_tags, list) else []:
        c = clean_tag(raw)
        if not c:
            continue
        k = tag_key(c)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(known.get(k, c))
        if len(out) >= MAX_TAGS:
            break
    return out


def has_tag(column, tag: str):
    """SQL condition: the JSON list in `column` contains exactly `tag`. Matches the tag's quoted JSON text, so it
    works the same on SQLite and Postgres and "llm" does not match "llms"; LIKE wildcards are escaped."""
    import json
    from sqlalchemy import String, cast
    return cast(column, String).contains(json.dumps(tag), autoescape=True)


def tags_in_use(db) -> list[str]:
    """Every tag already written by an official run, oldest run first, for spelling reuse. Reads runs rather than
    papers because a paper only receives its tags when its run finishes, and overlapping reviews must still see
    each other's spellings. Sandbox runs are left out so admin test reviews never shape the vocabulary."""
    from .models import ReviewRun
    seen: dict[str, None] = {}
    rows = (db.query(ReviewRun.topic_tags).filter(ReviewRun.sandbox.is_(False), ReviewRun.topic_tags.isnot(None))
            .order_by(ReviewRun.id))
    for (tags,) in rows:
        for t in tags or []:
            seen.setdefault(t, None)
    return list(seen)


def backfill_tags(db, limit: int | None = None, dry_run: bool = False, log=print) -> dict:
    """Tag public or publishable papers whose finished official run has no tags. Rejected papers are skipped (never
    shown). Idempotent: tagged papers are skipped, so it can be re-run after a failure or a spend-cap stop."""
    from sqlalchemy import or_
    from .harness.runner import Engine, SpendCapExceeded
    from .jobs import daily_spend_usd
    from .llm import LLMError
    from .models import Paper, ReviewRun
    from .settings_store import get_setting
    todo = []
    for p in (db.query(Paper).filter(Paper.official_run_id.isnot(None),
                                     or_(Paper.decision == "accept", Paper.source_kind == "arxiv"),
                                     Paper.status.notin_(("rejected", "desk_rejected", "withdrawn")))
              .order_by(Paper.id)):
        run = db.get(ReviewRun, p.official_run_id)
        if not p.topic_tags and run is not None and run.stage == "done":
            todo.append((p, run))
    if limit is not None:
        todo = todo[:limit]
    result = {"candidates": len(todo), "tagged": 0, "failed": 0, "stopped": "", "cost_usd": 0.0}
    for p, run in todo:
        if dry_run:
            log(f"{p.acr_id}  would tag  ({run.committee.get('chair', {}).get('model', '?')})  {p.title[:70]}")
            continue
        cap = float(get_setting(db, "spend_cap_daily_usd", 30.0) or 0)
        if cap > 0 and daily_spend_usd(db) >= cap:
            result["stopped"] = f"daily spend cap ${cap:.2f} reached"
            break
        before = run.total_cost_usd
        try:
            tags = Engine(db, run).backfill_topic_tags()
        except (LLMError, SpendCapExceeded) as e:
            db.rollback()
            result["failed"] += 1
            log(f"{p.acr_id}  FAILED  {e}")
            continue
        result["tagged"] += 1
        result["cost_usd"] = round(result["cost_usd"] + run.total_cost_usd - before, 6)
        log(f"{p.acr_id}  {', '.join(tags) or '(no usable tags)'}")
    return result
