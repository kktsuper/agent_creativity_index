"""Radar chart geometry for the four score axes. Pure computation; the SVG markup lives in templates/_radar.html.

Axes run clockwise from the top: originality, depth, implementation, potential impact. Adjacent pairs are the two
products the venue publishes: novelty = originality x depth (top-right), impact = potential impact x implementation
(bottom-left). The chair's final scores are the main shape; each reviewer's final scores are thin outlines so that
disagreement is visible at a glance.
"""
from __future__ import annotations

import math

AXES = [("originality", "Originality"), ("depth", "Depth"),
        ("implementation", "Implementation"), ("potential_impact", "Potential impact")]

W, H = 300, 250          # viewBox
CX, CY, R = 150, 122, 78  # centre and radius of the 10-point ring
LINE = 13                # label line height in viewBox units


def _clamp(v) -> float:
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def point(i: int, value) -> tuple[float, float]:
    """Vertex for axis i (0..3, clockwise from the top) at score `value` (0-10)."""
    ang = -math.pi / 2 + i * math.pi / 2
    r = R * _clamp(value) / 10
    return round(CX + r * math.cos(ang), 1), round(CY + r * math.sin(ang), 1)


def polygon(scores: dict) -> str:
    return " ".join(f"{x},{y}" for x, y in (point(i, scores.get(k)) for i, (k, _) in enumerate(AXES)))


def _label(i: int, name: str, value: float) -> dict:
    vx, vy = point(i, 10)
    lines = name.split() + [f"{value:.1f}"]
    n = len(lines)
    if i == 0:    # top: stack upward, centred
        anchor, x, y0 = "middle", vx, vy - 10 - (n - 1) * LINE
    elif i == 2:  # bottom: stack downward, centred
        anchor, x, y0 = "middle", vx, vy + 18
    elif i == 1:  # right: vertically centred, left-aligned
        anchor, x, y0 = "start", vx + 8, vy + 4 - (n - 1) * LINE / 2
    else:         # left: vertically centred, right-aligned
        anchor, x, y0 = "end", vx - 8, vy + 4 - (n - 1) * LINE / 2
    return {"anchor": anchor, "x": x, "lines": [{"text": t, "y": round(y0 + k * LINE, 1)} for k, t in enumerate(lines)]}


def radar_geometry(final: dict | None, reviewers: list[dict] | None = None) -> dict | None:
    """final: {axis: value} for the four axes. reviewers: [{"label": str, "scores": {axis: value}}].
    Returns everything the template needs, or None when the final scores are missing."""
    if not final or any(not isinstance(final.get(k), (int, float)) for k, _ in AXES):
        return None
    main_pts = [point(i, final[k]) for i, (k, _) in enumerate(AXES)]
    others = []
    for r in reviewers or []:
        s = r.get("scores") or {}
        if all(isinstance(s.get(k), (int, float)) for k, _ in AXES):
            others.append({"label": r.get("label", "reviewer"), "points": polygon(s),
                           "title": r.get("label", "reviewer") + ": " + ", ".join(f"{name} {s[k]:.1f}" for k, name in AXES)})
    return {
        "w": W, "h": H, "cx": CX, "cy": CY,
        "rings": [{"r": round(R * v / 10, 1), "label": str(int(v)) if v in (5, 10) else ""} for v in (2.5, 5, 7.5, 10)],
        "axes": [dict(zip(("x", "y"), point(i, 10))) for i in range(4)],
        "labels": [_label(i, name, final[k]) for i, (k, name) in enumerate(AXES)],
        "main": {"points": polygon(final),
                 "markers": [{"x": x, "y": y, "name": name, "value": f"{final[k]:.1f}"}
                             for (k, name), (x, y) in zip(AXES, main_pts)]},
        "others": others,
        "aria": "Final scores: " + ", ".join(f"{name} {final[k]:.1f} out of 10" for k, name in AXES),
    }
