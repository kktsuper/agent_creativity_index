"""The Index: author scores over 30- and 90-day windows, from public accepted papers by priority date."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from .models import Author, Paper, PortfolioComparison
from .scoring import aggregate
from .settings_store import get_setting
from .models import utcnow

WINDOWS = (30, 90)


def window_papers(db: Session, window_days: int, end: dt.datetime | None = None) -> list[Paper]:
    end = end or utcnow()
    start = end - dt.timedelta(days=window_days)
    from sqlalchemy import or_
    return (db.query(Paper).filter(Paper.public_at.isnot(None), or_(Paper.decision == "accept", Paper.source_kind == "arxiv"),
                                   Paper.priority_at >= start, Paper.priority_at <= end,
                                   Paper.score_creativity.isnot(None))
            .order_by(Paper.score_creativity.desc()).all())


def compute_index(db: Session, window_days: int, end: dt.datetime | None = None, aggregator: str | None = None) -> list[dict]:
    aggregator = aggregator or get_setting(db, "index_aggregator", "decay_half")
    by_author: dict[int, list[Paper]] = {}
    for p in window_papers(db, window_days, end):
        by_author.setdefault(p.author_id, []).append(p)
    bt = bradley_terry(db, window_days)
    rows = []
    for aid, papers in by_author.items():
        a = db.get(Author, aid)
        scores = [p.score_creativity for p in papers]
        rows.append({"author": a, "author_slug": a.slug, "author_name": a.name, "lab": a.lab,
                     "score": aggregate(aggregator, scores), "papers": len(papers),
                     "best": max(scores), "best_paper": papers[0],
                     "h2h": bt.get(aid)})
    rows.sort(key=lambda r: (-r["score"], -r["best"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def bradley_terry(db: Session, window_days: int, iters: int = 200) -> dict[int, float]:
    """Head-to-head rating from pairwise portfolio comparisons (phase 3). Returns {author_id: rating 0-100}."""
    comps = db.query(PortfolioComparison).filter(PortfolioComparison.window_days == window_days).all()
    if not comps:
        return {}
    ids = sorted({c.author_a_id for c in comps} | {c.author_b_id for c in comps})
    wins = {i: 0.0 for i in ids}
    games: dict[tuple[int, int], float] = {}
    for c in comps:
        a, b = c.author_a_id, c.author_b_id
        w = c.confidence if c.preference != "tie" else 0.5
        if c.preference == "a":
            wins[a] += w; wins[b] += 1 - w
        elif c.preference == "b":
            wins[b] += w; wins[a] += 1 - w
        else:
            wins[a] += 0.5; wins[b] += 0.5
        games[(a, b)] = games.get((a, b), 0) + 1
    p = {i: 1.0 for i in ids}
    for _ in range(iters):
        new = {}
        for i in ids:
            denom = 0.0
            for (a, b), n in games.items():
                if i in (a, b):
                    j = b if i == a else a
                    denom += n / (p[i] + p[j])
            new[i] = (wins[i] + 0.01) / (denom + 0.01)   # tiny prior keeps it finite
        s = sum(new.values()) / len(new)
        p = {i: v / s for i, v in new.items()}
    # map to 0-100 via logistic of log-rating
    import math
    return {i: round(100 * (1 / (1 + math.exp(-math.log(v)))), 1) for i, v in p.items()}
