"""Public site."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..api.read import search_query, stats as api_stats
from ..citations import cited_by, cites
from ..db import get_db
from ..harness.defaults import current_harness
from ..leaderboard import compute_index, WINDOWS
from ..models import Author, HarnessVersion, Paper, ReviewRun, ScoutRun
from ..scoring import AGGREGATOR_DESCRIPTIONS
from ..settings_store import get_setting
from ..tags import clean_tag
from .common import templates
from .radar import radar_geometry

router = APIRouter(include_in_schema=False)


def page(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    feed = db.query(Paper).filter(Paper.public_at.isnot(None)).order_by(Paper.public_at.desc()).limit(8).all()
    top = compute_index(db, 30)[:5]
    return page(request, "home.html", feed=feed, top=top, stats=api_stats(db), harness=current_harness(db))


@router.get("/papers", response_class=HTMLResponse)
def papers(request: Request, db: Session = Depends(get_db), q: str = "", field: str = "", type: str = "",
           sort: str = "recent", authors: str = "", tag: str = "", page_no: int = Query(1, alias="page", ge=1)):
    per = 25
    tag = clean_tag(tag)
    query = search_query(db, q, field, type, "", sort, authors, tag)
    total = query.count()
    rows = query.offset((page_no - 1) * per).limit(per).all()
    fields = [r[0] for r in db.query(Paper.field, func.count(Paper.id)).filter(Paper.public_at.isnot(None))
              .group_by(Paper.field).order_by(func.count(Paper.id).desc()).limit(30).all()]
    return page(request, "papers.html", papers=rows, total=total, page_no=page_no, pages=max(1, -(-total // per)),
                q=q, field=field, type=type, sort=sort, authors=authors, tag=tag, fields=fields)


@router.get("/papers/{acr_id}", response_class=HTMLResponse)
def paper(request: Request, acr_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
    if p is None or p.status in ("rejected", "desk_rejected", "withdrawn") or (p.decision == "reject" and p.source_kind != "arxiv"):
        return page(request, "404.html", status_code=404, what=f"No paper with ID {acr_id}")
    if p.public_at is None:
        return page(request, "paper_stub.html", paper=p)   # pending review or embargoed: ID, priority date, hash only
    run = db.get(ReviewRun, p.official_run_id) if p.official_run_id else None
    reviews = sorted(run.reviews, key=lambda r: r.slot) if run else []
    all_t = sorted(run.transcripts, key=lambda t: t.seq) if run else []
    failed = [t for t in all_t if (t.note or "").startswith("ERROR")]      # provider errors, no tokens billed
    transcripts = [t for t in all_t if not (t.note or "").startswith("ERROR")]
    scout = db.get(ScoutRun, p.scout_run_id) if p.scout_run_id else None
    radar = radar_geometry({"originality": p.score_originality, "depth": p.score_depth,
                            "potential_impact": p.score_potential_impact, "implementation": p.score_implementation},
                           [{"label": "Individual reviewer", "scores": r.scores} for r in reviews])
    return page(request, "paper.html", paper=p, run=run, reviews=reviews, transcripts=transcripts, failed=failed,
                cites=cites(db, p), cited_by=cited_by(db, p), scout=scout, radar=radar)


@router.get("/authors", response_class=HTMLResponse)
def authors(request: Request, db: Session = Depends(get_db), q: str = ""):
    query = db.query(Author)
    if q:
        query = query.filter(Author.name.ilike(f"%{q}%") | Author.lab.ilike(f"%{q}%"))
    rows = []
    for a in query.order_by(Author.created_at.desc()).limit(300).all():
        n = db.query(func.count(Paper.id)).filter(Paper.author_id == a.id, Paper.public_at.isnot(None)).scalar()
        rows.append((a, n))
    return page(request, "authors.html", rows=rows, q=q)


@router.get("/authors/{slug}", response_class=HTMLResponse)
def author(request: Request, slug: str, db: Session = Depends(get_db)):
    a = db.query(Author).filter(Author.slug == slug).first()
    if a is None:
        return page(request, "404.html", status_code=404, what=f"No author {slug}")
    papers = db.query(Paper).filter(Paper.author_id == a.id, Paper.public_at.isnot(None)).order_by(Paper.priority_at.desc()).all()
    pending = db.query(func.count(Paper.id)).filter(Paper.author_id == a.id, Paper.public_at.is_(None),
                                                    Paper.status.notin_(["withdrawn", "desk_rejected", "rejected"])).scalar()
    idx = {w: next((r for r in compute_index(db, w) if r["author_slug"] == slug), None) for w in WINDOWS}
    return page(request, "author.html", author=a, papers=papers, pending=pending, idx=idx)


@router.get("/index", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), window: int = 30):
    window = window if window in WINDOWS else 30
    agg = get_setting(db, "index_aggregator")
    return page(request, "index.html", rows=compute_index(db, window), window=window, windows=WINDOWS,
                aggregator=agg, aggregator_desc=AGGREGATOR_DESCRIPTIONS.get(agg, ""))


@router.get("/review", response_class=HTMLResponse)
def review(request: Request, db: Session = Depends(get_db)):
    hv = current_harness(db)
    history = db.query(HarnessVersion).filter(HarnessVersion.is_draft.is_(False)).order_by(HarnessVersion.published_at.desc()).all()
    return page(request, "review.html", harness=hv, history=history, rubric=hv.config["rubric"],
                rebuttal=hv.config["rebuttal"], labs=hv.config["committee"]["labs"])


@router.get("/review/harness/{version}", response_class=HTMLResponse)
def harness_version(request: Request, version: str, db: Session = Depends(get_db)):
    hv = db.query(HarnessVersion).filter(HarnessVersion.version == version, HarnessVersion.is_draft.is_(False)).first()
    if hv is None:
        return page(request, "404.html", status_code=404, what=f"No published harness {version}")
    return page(request, "harness.html", harness=hv)


@router.get("/submit", response_class=HTMLResponse)
def submit(request: Request, db: Session = Depends(get_db)):
    return page(request, "submit.html", rate_limit=get_setting(db, "rate_limit_enabled"),
                per_day=get_setting(db, "rate_limit_submissions_per_day"),
                needs_token=bool(templates.env.globals["settings"]().registration_token))


@router.get("/scout", response_class=HTMLResponse)
def scout_list(request: Request, db: Session = Depends(get_db)):
    runs = db.query(ScoutRun).order_by(ScoutRun.id.desc()).limit(50).all()
    return page(request, "scout_list.html", runs=runs)


@router.get("/scout/{run_id}", response_class=HTMLResponse)
def scout_run(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScoutRun, run_id)
    if run is None:
        return page(request, "404.html", status_code=404, what=f"No scout run {run_id}")
    papers = {p.external_id: p for p in db.query(Paper).filter(Paper.scout_run_id == run.id).all()}
    return page(request, "scout_run.html", run=run, papers=papers)


@router.get("/rules", response_class=HTMLResponse)
def rules(request: Request):
    return page(request, "rules.html")
