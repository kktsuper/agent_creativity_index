"""Citation graph: papers cite each other by ACR ID in their text. Re-indexed whenever a paper becomes public."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .ids import find_acr_ids
from .models import Citation, Paper


def reindex_paper(db: Session, paper: Paper) -> int:
    text = f"{paper.abstract}\n{paper.body_md}"
    ids = [i for i in find_acr_ids(text) if i != paper.acr_id]
    for d in paper.author_declared_related or []:
        if d not in ids and d != paper.acr_id:
            ids.append(d)
    db.query(Citation).filter(Citation.from_paper_id == paper.id).delete()
    for acr_id in ids:
        idx = text.find(acr_id)
        ctx = text[max(0, idx - 120): idx + 140].replace("\n", " ") if idx >= 0 else "(declared related work)"
        db.add(Citation(from_paper_id=paper.id, to_acr_id=acr_id, context=ctx))
    db.flush()
    return len(ids)


def cited_by(db: Session, paper: Paper) -> list[Paper]:
    rows = db.query(Citation).filter(Citation.to_acr_id == paper.acr_id).all()
    out = []
    for c in rows:
        p = db.get(Paper, c.from_paper_id)
        if p and p.public_at is not None:
            out.append(p)
    return out


def cites(db: Session, paper: Paper) -> list[dict]:
    rows = db.query(Citation).filter(Citation.from_paper_id == paper.id).all()
    out = []
    for c in rows:
        target = db.query(Paper).filter(Paper.acr_id == c.to_acr_id).first()
        out.append({"acr_id": c.to_acr_id, "paper": target if (target and target.public_at) else None,
                    "context": c.context})
    return out
