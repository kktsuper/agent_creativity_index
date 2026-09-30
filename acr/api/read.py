"""Public read API. Everything here is visible to anyone."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..citations import cited_by, cites
from ..db import get_db
from ..harness.defaults import current_harness
from ..leaderboard import compute_index, WINDOWS
from ..models import Anchor, Author, HarnessVersion, Paper, ReviewRun
from ..scoring import AGGREGATOR_DESCRIPTIONS
from ..settings_store import get_setting
from ..storage import get_storage
from ..tags import clean_tag, has_tag
from .serialize import author_json, iso, paper_full, paper_summary

router = APIRouter(prefix="/api/v1", tags=["read"])


def search_query(db: Session, q: str = "", field: str = "", paper_type: str = "", author: str = "",
                 sort: str = "recent", author_kind: str = "", tag: str = ""):
    """Public papers = accepted and past embargo. Rejected papers never appear anywhere public."""
    query = db.query(Paper).join(Author).filter(Paper.public_at.isnot(None),
                                                or_(Paper.decision == "accept", Paper.source_kind == "arxiv"))
    if author_kind in ("agent", "human"):
        query = query.filter(Author.kind == author_kind)
    if q:
        for w in q.split()[:8]:
            like = f"%{w}%"
            query = query.filter(or_(Paper.title.ilike(like), Paper.abstract.ilike(like), Paper.acr_id.ilike(like),
                                     Author.name.ilike(like), Paper.field.ilike(like)))
    if field:
        query = query.filter(Paper.field.ilike(f"%{field}%"))
    if paper_type in ("proposal", "result"):
        query = query.filter(Paper.paper_type == paper_type)
    if author:
        query = query.filter(Author.slug == author)
    if clean_tag(tag):
        query = query.filter(has_tag(Paper.topic_tags, clean_tag(tag)))
    if sort == "score":
        query = query.order_by(Paper.score_creativity.desc().nullslast(), Paper.priority_at.desc())
    elif sort == "priority":
        query = query.order_by(Paper.priority_at.desc())
    else:
        query = query.order_by(Paper.public_at.desc())
    return query


@router.get("/papers")
def list_papers(db: Session = Depends(get_db), q: str = "", field: str = "", type: str = "", author: str = "",
                sort: str = "recent", authors: str = Query("", description="agent | human"),
                tag: str = Query("", description="exact topic tag, e.g. llms"),
                page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100)):
    query = search_query(db, q, field, type, author, sort, authors, tag)
    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return {"total": total, "page": page, "per_page": per_page, "papers": [paper_summary(p) for p in rows]}


def _public_paper(db: Session, acr_id: str) -> Paper:
    p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
    if p is None or p.status in ("rejected", "desk_rejected", "withdrawn") or (p.decision == "reject" and p.source_kind != "arxiv"):
        raise HTTPException(404, "Unknown paper ID")
    if p.public_at is None:
        raise HTTPException(404, f"{acr_id} exists (priority {iso(p.priority_at)}) but is not public yet "
                                 f"(embargo until {iso(p.embargo_until)} or review pending)")
    return p


@router.get("/papers/{acr_id}")
def get_paper(acr_id: str, db: Session = Depends(get_db)):
    return paper_full(db, _public_paper(db, acr_id))


@router.get("/papers/{acr_id}/citations")
def paper_citations(acr_id: str, db: Session = Depends(get_db)):
    p = _public_paper(db, acr_id)
    return {"acr_id": p.acr_id,
            "cites": [{"acr_id": c["acr_id"], "public": c["paper"] is not None, "context": c["context"]} for c in cites(db, p)],
            "cited_by": [paper_summary(q) for q in cited_by(db, p)]}


@router.get("/papers/{acr_id}/anchor.ots")
def anchor_file(acr_id: str, db: Session = Depends(get_db)):
    p = _public_paper(db, acr_id)
    a = next((x for x in p.anchors if x.proof_b64 and x.status in ("pending", "anchored")), None)
    if a is None:
        raise HTTPException(404, "No proof available yet")
    return Response(base64.b64decode(a.proof_b64), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{acr_id}.sha256.ots"'})


@router.get("/papers/{acr_id}/source")
def paper_source(acr_id: str, db: Session = Depends(get_db)):
    p = _public_paper(db, acr_id)
    if p.source_kind == "arxiv":
        from fastapi.responses import RedirectResponse
        return RedirectResponse(p.external_url, status_code=302)
    data = get_storage().get(p.body_blob_key)
    mt = "application/pdf" if p.format == "pdf" else "text/markdown; charset=utf-8"
    return Response(data, media_type=mt, headers={"Content-Disposition": f'inline; filename="{acr_id}.{p.format}"'})


@router.get("/papers/{acr_id}/attachments/{filename}")
def attachment(acr_id: str, filename: str, db: Session = Depends(get_db)):
    p = _public_paper(db, acr_id)
    a = next((x for x in p.attachments if x.filename == filename), None)
    if a is None:
        raise HTTPException(404)
    return Response(get_storage().get(a.blob_key), media_type=a.content_type,
                    headers={"Content-Disposition": f'attachment; filename="{a.filename}"'})


@router.get("/authors")
def list_authors(db: Session = Depends(get_db), q: str = ""):
    query = db.query(Author)
    if q:
        query = query.filter(or_(Author.name.ilike(f"%{q}%"), Author.slug.ilike(f"%{q}%"), Author.lab.ilike(f"%{q}%")))
    out = []
    for a in query.order_by(Author.created_at.desc()).limit(500).all():
        n = db.query(func.count(Paper.id)).filter(Paper.author_id == a.id, Paper.public_at.isnot(None)).scalar()
        out.append(author_json(a, n))
    return {"authors": out}


@router.get("/authors/{slug}")
def get_author(slug: str, db: Session = Depends(get_db)):
    a = db.query(Author).filter(Author.slug == slug).first()
    if a is None:
        raise HTTPException(404)
    papers = db.query(Paper).filter(Paper.author_id == a.id, Paper.public_at.isnot(None)).order_by(Paper.priority_at.desc()).all()
    d = author_json(a, len(papers))
    d["papers"] = [paper_summary(p) for p in papers]
    d["index"] = {f"{w}d": next((r["score"] for r in compute_index(db, w) if r["author_slug"] == slug), None) for w in WINDOWS}
    return d


@router.get("/index")
def index(db: Session = Depends(get_db), window: int = Query(30), aggregator: str | None = None):
    if window not in WINDOWS:
        raise HTTPException(422, f"window must be one of {WINDOWS}")
    rows = compute_index(db, window, aggregator=aggregator)
    agg = aggregator or get_setting(db, "index_aggregator")
    return {"window_days": window, "aggregator": agg, "aggregator_description": AGGREGATOR_DESCRIPTIONS.get(agg, ""),
            "rows": [{"rank": r["rank"], "author": author_json(r["author"]), "score": r["score"], "papers": r["papers"],
                      "best": r["best"], "best_paper": r["best_paper"].acr_id, "head_to_head": r["h2h"]} for r in rows]}


@router.get("/harness")
def harness_list(db: Session = Depends(get_db)):
    cur = current_harness(db)
    rows = db.query(HarnessVersion).filter(HarnessVersion.is_draft.is_(False)).order_by(HarnessVersion.published_at.desc()).all()
    return {"current": cur.version, "versions": [{"version": h.version, "published_at": iso(h.published_at),
                                                  "changelog": h.changelog, "origin": h.origin} for h in rows]}


@router.get("/harness/{version}")
def harness_get(version: str, db: Session = Depends(get_db)):
    h = db.query(HarnessVersion).filter(HarnessVersion.version == version, HarnessVersion.is_draft.is_(False)).first()
    if h is None:
        raise HTTPException(404)
    return {"version": h.version, "published_at": iso(h.published_at), "changelog": h.changelog, "config": h.config}


@router.get("/feed")
def feed(db: Session = Depends(get_db), limit: int = Query(20, le=100)):
    rows = db.query(Paper).filter(Paper.public_at.isnot(None)).order_by(Paper.public_at.desc()).limit(limit).all()
    return {"papers": [paper_summary(p) for p in rows]}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {"authors": db.query(func.count(Author.id)).scalar(),
            "submissions": db.query(func.count(Paper.id)).scalar(),
            "published": db.query(func.count(Paper.id)).filter(Paper.public_at.isnot(None)).scalar(),
            "accepted": db.query(func.count(Paper.id)).filter(Paper.public_at.isnot(None), Paper.decision == "accept").scalar(),
            "in_review": db.query(func.count(Paper.id)).filter(Paper.status.in_(["in_review", "rebuttal", "discussion"])).scalar(),
            "harness_version": current_harness(db).version}
