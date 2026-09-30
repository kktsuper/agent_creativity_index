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


def tags_in_use(db) -> list[str]:
    """Every tag already stored on a paper, in first-seen order, for spelling reuse."""
    from .models import Paper
    seen: dict[str, None] = {}
    for (tags,) in db.query(Paper.topic_tags).filter(Paper.topic_tags.isnot(None)):
        for t in tags or []:
            seen.setdefault(t, None)
    return list(seen)
