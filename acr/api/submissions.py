from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import author_from_request
from ..db import get_db
from ..harness.runner import Engine
from ..jobs import enqueue
from ..models import Author, Paper, ReviewRun
from ..ratelimit import check_and_record
from ..submissions import SubmissionError, create_submission
from .serialize import iso, paper_full, run_json
from ..models import utcnow

router = APIRouter(prefix="/api/v1", tags=["submissions"])


def _own_paper(db: Session, author: Author, acr_id: str) -> Paper:
    p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
    if p is None or p.author_id != author.id:
        raise HTTPException(404, "No such submission for this key")
    return p


@router.post("/submissions", status_code=201)
async def submit(
    author: Author = Depends(author_from_request), db: Session = Depends(get_db),
    title: str = Form(..., max_length=400), abstract: str = Form(...),
    paper_type: str = Form(..., description="proposal | result"), field: str = Form(..., max_length=120),
    keywords: str = Form("", description="comma-separated"), embargo_months: int = Form(0, ge=0, le=12),
    related: str = Form("", description="comma-separated ACR IDs this work builds on"),
    paper: UploadFile = File(..., description=".md or .pdf"),
    code: list[UploadFile] = File(default=[]), data: list[UploadFile] = File(default=[]),
):
    check_and_record(db, author, "submit")
    name = (paper.filename or "").lower()
    fmt = "pdf" if name.endswith(".pdf") or paper.content_type == "application/pdf" else "md"
    body = await paper.read()
    atts = []
    for kind, files in (("code", code), ("data", data)):
        for f in files:
            if f.filename:
                atts.append({"kind": kind, "filename": f.filename.replace("/", "_")[:200],
                             "content_type": f.content_type, "data": await f.read()})
    try:
        p = create_submission(db, author, title=title, abstract=abstract, paper_type=paper_type.strip().lower(),
                              field=field, keywords=[k for k in keywords.split(",")], fmt=fmt, body=body,
                              attachments=atts, embargo_months=embargo_months,
                              related=[r.strip() for r in related.split(",") if r.strip()])
    except SubmissionError as e:
        raise HTTPException(422, str(e))
    return {"acr_id": p.acr_id, "priority_at": iso(p.priority_at), "content_hash": p.content_hash,
            "embargo_until": iso(p.embargo_until), "status": p.status, "word_count": p.word_count,
            "note": "The content hash is being anchored to OpenTimestamps; the proof appears on the paper page. "
                    "Desk screen runs within a minute; reviews follow. Poll GET /api/v1/submissions/{acr_id}."}


@router.get("/submissions")
def list_mine(author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    rows = db.query(Paper).filter(Paper.author_id == author.id).order_by(Paper.id.desc()).all()
    return {"submissions": [{"acr_id": p.acr_id, "title": p.title, "status": p.status, "decision": p.decision,
                             "priority_at": iso(p.priority_at), "embargo_until": iso(p.embargo_until),
                             "public_at": iso(p.public_at), "creativity": p.score_creativity} for p in rows]}


@router.get("/submissions/{acr_id}")
def get_mine(acr_id: str, author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    p = _own_paper(db, author, acr_id)
    d = paper_full(db, p, public_view=False)
    run = db.get(ReviewRun, p.official_run_id) if p.official_run_id else None
    # Before the rebuttal opens the author sees only status; reviews are hidden to keep the process blind.
    if run and run.stage in ("assigned", "prior_art", "reviews"):
        d["review"] = {"stage": run.stage, "harness_version": run.harness_version, "note": "reviews in progress"}
    elif run:
        d["review"] = run_json(run, include_transcripts=(run.stage == "done"))
        if run.stage != "done":
            d["review"]["reviews"] = [{"slot": r["slot"], "lab": r["lab"], "model": r["model"], "initial": r["initial"]}
                                      for r in d["review"]["reviews"]]
    return d


class RebuttalIn(BaseModel):
    text: str = Field(min_length=1, max_length=40000)


@router.post("/submissions/{acr_id}/rebuttal")
def rebuttal(acr_id: str, body: RebuttalIn, author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    p = _own_paper(db, author, acr_id)
    run = db.get(ReviewRun, p.official_run_id) if p.official_run_id else None
    if run is None or run.stage != "rebuttal":
        raise HTTPException(409, f"Rebuttal is not open (status {p.status})")
    eng = Engine(db, run)
    if not eng.rebuttal_open():
        raise HTTPException(409, "Rebuttal window closed or already used")
    try:
        eng.submit_rebuttal(body.text)
    except ValueError as e:
        raise HTTPException(422, str(e))
    enqueue(db, "advance_run", {"run_id": run.id})
    return {"ok": True, "tokens": run.rebuttal_tokens, "submitted_at": iso(run.rebuttal_submitted_at)}


@router.post("/submissions/{acr_id}/rebuttal/waive")
def waive(acr_id: str, author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    p = _own_paper(db, author, acr_id)
    run = db.get(ReviewRun, p.official_run_id) if p.official_run_id else None
    if run is None or run.stage != "rebuttal" or run.rebuttal_submitted_at:
        raise HTTPException(409, "Nothing to waive")
    run.rebuttal_deadline = utcnow()
    enqueue(db, "advance_run", {"run_id": run.id})
    return {"ok": True}


@router.post("/submissions/{acr_id}/withdraw")
def withdraw(acr_id: str, author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    p = _own_paper(db, author, acr_id)
    if p.decision is not None or p.public_at is not None:
        raise HTTPException(409, "Cannot withdraw after a decision; the record is permanent")
    p.status = "withdrawn"
    p.status_reason = "withdrawn by author"
    return {"ok": True}
