"""Scoring formulas and pluggable author-score aggregators.

Axes (each 0-10, one decimal):
  originality, depth, potential_impact, implementation
Derived:
  novelty    = originality * depth            (0-100)
  impact     = potential_impact * implementation   (0-100)
  creativity = novelty * impact / 100          (0-100)

The /100 keeps creativity on a 0-100 scale so it is readable; the ordering is identical
to the raw product. One creativity score per paper, fixed at decision time.
"""
from __future__ import annotations

from typing import Callable, Iterable

AXES = ("originality", "depth", "potential_impact", "implementation")


def clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, float(x)))


def derive(scores: dict) -> dict:
    o = clamp(scores.get("originality", 0))
    d = clamp(scores.get("depth", 0))
    p = clamp(scores.get("potential_impact", 0))
    i = clamp(scores.get("implementation", 0))
    novelty = o * d
    impact = p * i
    creativity = novelty * impact / 100.0
    return {
        "originality": round(o, 2), "depth": round(d, 2),
        "potential_impact": round(p, 2), "implementation": round(i, 2),
        "novelty": round(novelty, 2), "impact": round(impact, 2),
        "creativity": round(creativity, 2),
    }


def mean_axes(score_dicts: Iterable[dict]) -> dict:
    rows = [s for s in score_dicts if s]
    if not rows:
        return {a: 0.0 for a in AXES}
    return {a: sum(clamp(r.get(a, 0)) for r in rows) / len(rows) for a in AXES}


# ----------------------------------------------------------------------------- aggregators
# An aggregator maps the list of creativity scores of an author's public, accepted papers
# whose priority date falls in the window -> one author score (0-100).

Aggregator = Callable[[list[float]], float]


def agg_decay_half(scores: list[float]) -> float:
    """Sort descending; weight 1/2, 1/4, 1/8, ... Rewards a great paper most, more papers with
    diminishing returns, and never rewards padding with weak work (a weak paper adds ~nothing).
    Max approaches 100."""
    s = sorted(scores, reverse=True)
    return sum(v / (2 ** (k + 1)) for k, v in enumerate(s))


def agg_top3_mean(scores: list[float]) -> float:
    s = sorted(scores, reverse=True)[:3]
    return sum(s) / len(s) if s else 0.0


def agg_max(scores: list[float]) -> float:
    return max(scores) if scores else 0.0


def agg_sum(scores: list[float]) -> float:
    return float(sum(scores))


def agg_mean(scores: list[float]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


AGGREGATORS: dict[str, Aggregator] = {
    "decay_half": agg_decay_half,
    "top3_mean": agg_top3_mean,
    "max": agg_max,
    "sum": agg_sum,
    "mean": agg_mean,
}

AGGREGATOR_DESCRIPTIONS = {
    "decay_half": "Best paper counts 1/2, next 1/4, then 1/8, and so on. Default.",
    "top3_mean": "Mean of the three best creativity scores.",
    "max": "Best single paper.",
    "sum": "Sum of all creativity scores (rewards volume).",
    "mean": "Mean of all creativity scores.",
}


def aggregate(name: str, scores: list[float]) -> float:
    fn = AGGREGATORS.get(name) or AGGREGATORS["decay_half"]
    return round(fn([float(x) for x in scores]), 2)
