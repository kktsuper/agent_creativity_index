"""Private admin dashboard (HTML forms). Login with ACR_ADMIN_PASSWORD."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import check_admin_password, make_admin_cookie, require_admin
from ..db import get_db
from ..harness.committee import assign_committee, CommitteeError
from ..harness.defaults import current_harness
from ..jobs import enqueue
from ..llm import provider_available
from ..models import (AuditLog, Author, HarnessVersion, ImprovementReport, Job, Paper, PortfolioComparison,
                      ReviewRun)
from ..scoring import AGGREGATORS
from ..settings_store import all_settings, set_setting
from .common import templates
from ..models import utcnow

router = APIRouter(prefix="/admin", include_in_schema=False)


def page(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def audit(db: Session, action: str, target: str = "", **detail):
    db.add(AuditLog(actor="admin", action=action, target=target, detail=detail))


# ----------------------------------------------------------------------------- auth
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "admin/login.html", error=None)


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    if not check_admin_password(password):
        return page(request, "admin/login.html", error="Wrong password")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("acr_admin", make_admin_cookie(), httponly=True, samesite="lax", max_age=12 * 3600)
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("acr_admin")
    return resp


# ----------------------------------------------------------------------------- dashboard
@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def dashboard(request: Request, db: Session = Depends(get_db)):
    hv = current_harness(db)
    providers = [(m, *provider_available(m)) for m in hv.config["committee"]["labs"]]
    counts = dict(db.query(Paper.status, func.count(Paper.id)).group_by(Paper.status).all())
    order = ["received", "in_review", "rebuttal", "discussion", "decided", "published", "rejected", "desk_rejected", "withdrawn"]
    counts = dict(sorted(counts.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else len(order), kv[0])))
    recent = db.query(Paper).order_by(Paper.id.desc()).limit(15).all()
    failed_jobs = db.query(Job).filter(Job.status == "failed").order_by(Job.id.desc()).limit(10).all()
    queued = db.query(func.count(Job.id)).filter(Job.status.in_(["queued", "running"])).scalar()
    from ..jobs import daily_spend_usd
    from ..models import Transcript
    total_spend = float(db.query(func.coalesce(func.sum(Transcript.cost_usd), 0.0)).scalar() or 0)
    return page(request, "admin/dashboard.html", harness=hv, providers=providers, counts=counts, recent=recent,
                failed_jobs=failed_jobs, queued=queued, settings_=all_settings(db), aggregators=list(AGGREGATORS),
                spend_today=daily_spend_usd(db), spend_total=total_spend)


@router.post("/settings", dependencies=[Depends(require_admin)])
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    set_setting(db, "rate_limit_enabled", form.get("rate_limit_enabled") == "on")
    set_setting(db, "rate_limit_submissions_per_day", int(form.get("rate_limit_submissions_per_day", 20)))
    set_setting(db, "index_aggregator", form.get("index_aggregator", "decay_half"))
    set_setting(db, "auto_review", form.get("auto_review") == "on")
    set_setting(db, "auto_publish", form.get("auto_publish") == "on")
    set_setting(db, "spend_cap_per_run_usd", float(form.get("spend_cap_per_run_usd", 3.0) or 0))
    set_setting(db, "spend_cap_daily_usd", float(form.get("spend_cap_daily_usd", 30.0) or 0))
    audit(db, "settings", detail=dict(form))
    return RedirectResponse("/admin", status_code=303)


# ----------------------------------------------------------------------------- papers
@router.get("/papers", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def papers(request: Request, db: Session = Depends(get_db), status: str = "", q: str = ""):
    query = db.query(Paper)
    if status:
        query = query.filter(Paper.status == status)
    if q:
        query = query.filter(Paper.title.ilike(f"%{q}%") | Paper.acr_id.ilike(f"%{q}%"))
    rows = query.order_by(Paper.id.desc()).limit(200).all()
    return page(request, "admin/papers.html", papers=rows, status=status, q=q)


@router.get("/papers/{acr_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def paper(request: Request, acr_id: str, db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
    if p is None:
        return page(request, "404.html", status_code=404, what=acr_id)
    runs = db.query(ReviewRun).filter(ReviewRun.paper_id == p.id).order_by(ReviewRun.id.desc()).all()
    harnesses = db.query(HarnessVersion).order_by(HarnessVersion.id.desc()).all()
    jobs = db.query(Job).filter(Job.payload["paper_id"].as_string() == str(p.id)).order_by(Job.id.desc()).limit(20).all()
    return page(request, "admin/paper.html", paper=p, runs=runs, harnesses=harnesses, jobs=jobs)


@router.post("/papers/{acr_id}/action", dependencies=[Depends(require_admin)])
def paper_action(acr_id: str, action: str = Form(...), harness_id: int | None = Form(None), db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
    if p is None:
        return RedirectResponse("/admin/papers", status_code=303)
    if action == "screen_pass":
        p.status, p.status_reason, p.decision, p.decided_at = "received", "admin override: screen passed", None, None
        enqueue(db, "start_review", {"paper_id": p.id})
    elif action == "start_review":
        if p.official_run_id is None:
            enqueue(db, "start_review", {"paper_id": p.id})
    elif action == "retry_run" and p.official_run_id:
        run = db.get(ReviewRun, p.official_run_id)
        if run and run.stage == "failed":
            run.stage = _stage_before_failure(run)
            run.error = ""
            enqueue(db, "advance_run", {"run_id": run.id})
    elif action == "reset_run":
        p.official_run_id, p.status, p.status_reason = None, "received", "admin reset; new official run"
        p.decision = p.decided_at = None
        enqueue(db, "start_review", {"paper_id": p.id})
    elif action == "rereview":
        from ..jobs import rereview_paper
        rereview_paper(db, p, "re-review requested by admin")
    elif action == "test_review":
        enqueue(db, "start_review", {"paper_id": p.id, "sandbox": True, "harness_version_id": harness_id})
    elif action == "publish_now":
        if p.status == "decided":
            p.embargo_until = utcnow()
            enqueue(db, "publish", {"paper_id": p.id, "force": True})
    elif action == "withdraw":
        if p.public_at is None:
            p.status, p.status_reason = "withdrawn", "withdrawn by admin"
    elif action == "reanchor":
        enqueue(db, "anchor", {"paper_id": p.id})
    elif action == "reindex":
        enqueue(db, "reindex_citations", {"paper_id": p.id})
    audit(db, f"paper.{action}", acr_id, harness_id=harness_id)
    return RedirectResponse(f"/admin/papers/{acr_id}", status_code=303)


def _stage_before_failure(run: ReviewRun) -> str:
    # infer from what was completed
    if run.decision:
        return "decision"
    if any(r.final for r in run.reviews):
        return "discussion"
    if run.rebuttal_opens_at:
        return "rebuttal"
    if run.reviews:
        return "reviews"
    if run.prior_art:
        return "prior_art"
    return "assigned"


@router.get("/runs/{run_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def run_view(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ReviewRun, run_id)
    if run is None:
        return page(request, "404.html", status_code=404, what=f"run {run_id}")
    return page(request, "admin/run.html", run=run, paper=db.get(Paper, run.paper_id),
                reviews=sorted(run.reviews, key=lambda r: r.slot), transcripts=sorted(run.transcripts, key=lambda t: t.seq))


@router.post("/runs/{run_id}/advance", dependencies=[Depends(require_admin)])
def run_advance(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ReviewRun, run_id)
    if run and run.stage == "rebuttal":
        run.rebuttal_deadline = utcnow()
    if run and run.stage not in ("done",):
        if run.stage == "failed":
            run.stage, run.error = _stage_before_failure(run), ""
        enqueue(db, "advance_run", {"run_id": run.id})
    return RedirectResponse(f"/admin/runs/{run_id}", status_code=303)


# ----------------------------------------------------------------------------- harness
@router.get("/harness", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def harness_list(request: Request, db: Session = Depends(get_db)):
    rows = db.query(HarnessVersion).order_by(HarnessVersion.id.desc()).all()
    return page(request, "admin/harness_list.html", versions=rows)


@router.post("/harness/new-draft", dependencies=[Depends(require_admin)])
def new_draft(from_version: str = Form(...), db: Session = Depends(get_db)):
    base = db.query(HarnessVersion).filter(HarnessVersion.version == from_version).first()
    from ..phase3 import next_draft_version
    v = next_draft_version(db, base.version.split("-")[0])
    hv = HarnessVersion(version=v, config=json.loads(json.dumps(base.config)), is_draft=True, origin="admin",
                        parent_version=base.version, changelog="")
    db.add(hv); db.flush()
    audit(db, "harness.new_draft", v, from_version=from_version)
    return RedirectResponse(f"/admin/harness/{v}", status_code=303)


@router.get("/harness/{version}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def harness_edit(request: Request, version: str, db: Session = Depends(get_db)):
    hv = db.query(HarnessVersion).filter(HarnessVersion.version == version).first()
    if hv is None:
        return page(request, "404.html", status_code=404, what=version)
    papers = db.query(Paper).filter(Paper.status.notin_(["withdrawn"])).order_by(Paper.id.desc()).limit(100).all()
    probe = {}
    for lab in ["independent"] + [m["lab"] for m in hv.config["committee"]["labs"]]:
        try:
            c = assign_committee(hv.config, lab, 0)
            probe[lab] = f"OK — reviewers {[r['lab'] for r in c['reviewers']]}, chair {c['chair']['lab']}"
        except CommitteeError as e:
            probe[lab] = f"FAIL — {e}"
    return page(request, "admin/harness_edit.html", hv=hv, config_json=json.dumps(hv.config, indent=2), papers=papers,
                probe=probe, error=None)


@router.post("/harness/{version}/save", dependencies=[Depends(require_admin)])
async def harness_save(request: Request, version: str, db: Session = Depends(get_db)):
    hv = db.query(HarnessVersion).filter(HarnessVersion.version == version).first()
    form = await request.form()
    if hv is None or not hv.is_draft:
        return RedirectResponse("/admin/harness", status_code=303)
    try:
        cfg = json.loads(form.get("config_json", ""))
        for k in ("committee", "rubric", "prompts", "rebuttal", "discussion", "limits", "prior_art"):
            if k not in cfg:
                raise ValueError(f"missing top-level key {k!r}")
        if int(cfg["rebuttal"].get("hours", 24)) > 24 * 14:
            raise ValueError("rebuttal.hours unreasonably large")
    except Exception as e:
        papers = db.query(Paper).order_by(Paper.id.desc()).limit(100).all()
        return page(request, "admin/harness_edit.html", hv=hv, config_json=form.get("config_json", ""), papers=papers,
                    probe={}, error=f"Invalid config: {e}")
    hv.config = cfg
    hv.changelog = form.get("changelog", "")
    audit(db, "harness.save_draft", version)
    return RedirectResponse(f"/admin/harness/{version}", status_code=303)


@router.post("/harness/{version}/publish", dependencies=[Depends(require_admin)])
def harness_publish(version: str, new_version: str = Form(...), changelog: str = Form(...), db: Session = Depends(get_db)):
    hv = db.query(HarnessVersion).filter(HarnessVersion.version == version).first()
    if hv is None or not hv.is_draft:
        return RedirectResponse("/admin/harness", status_code=303)
    new_version = new_version.strip()
    if db.query(HarnessVersion).filter(HarnessVersion.version == new_version, HarnessVersion.id != hv.id).first():
        new_version = f"{new_version}+{hv.id}"
    for other in db.query(HarnessVersion).filter(HarnessVersion.is_current.is_(True)).all():
        other.is_current = False
    hv.version, hv.changelog, hv.is_draft, hv.is_current = new_version, changelog, False, True
    hv.published_at = utcnow()
    for run in db.query(ReviewRun).filter(ReviewRun.harness_version_id == hv.id).all():
        run.harness_version = new_version   # sandbox runs made against the draft carry the final name
    audit(db, "harness.publish", new_version)
    return RedirectResponse("/admin/harness", status_code=303)


@router.post("/harness/{version}/discard", dependencies=[Depends(require_admin)])
def harness_discard(version: str, db: Session = Depends(get_db)):
    hv = db.query(HarnessVersion).filter(HarnessVersion.version == version, HarnessVersion.is_draft.is_(True)).first()
    if hv and not db.query(ReviewRun).filter(ReviewRun.harness_version_id == hv.id).first():
        db.delete(hv)
        audit(db, "harness.discard", version)
    return RedirectResponse("/admin/harness", status_code=303)


@router.post("/test-review", dependencies=[Depends(require_admin)])
def test_review(acr_id: str = Form(...), harness_id: int = Form(...), db: Session = Depends(get_db)):
    p = db.query(Paper).filter(Paper.acr_id == acr_id.strip()).first()
    if p:
        enqueue(db, "start_review", {"paper_id": p.id, "sandbox": True, "harness_version_id": harness_id})
        audit(db, "test_review", acr_id, harness_id=harness_id)
        return RedirectResponse(f"/admin/papers/{p.acr_id}", status_code=303)
    return RedirectResponse("/admin/harness", status_code=303)


# ----------------------------------------------------------------------------- phase 3
@router.get("/improve", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def improve_list(request: Request, db: Session = Depends(get_db)):
    reports = db.query(ImprovementReport).order_by(ImprovementReport.id.desc()).limit(50).all()
    comps = db.query(PortfolioComparison).order_by(PortfolioComparison.id.desc()).limit(50).all()
    authors = {a.id: a for a in db.query(Author).all()}
    return page(request, "admin/improve.html", reports=reports, comps=comps, authors=authors, harness=current_harness(db))


@router.post("/improve/start", dependencies=[Depends(require_admin)])
def improve_start(db: Session = Depends(get_db)):
    rep = ImprovementReport(base_version=current_harness(db).version)
    db.add(rep); db.flush()
    enqueue(db, "harness_improve", {"report_id": rep.id}, max_attempts=1)
    audit(db, "improve.start", str(rep.id))
    return RedirectResponse("/admin/improve", status_code=303)


@router.post("/pairwise/start", dependencies=[Depends(require_admin)])
def pairwise_start(window_days: int = Form(90), max_pairs: int = Form(20), db: Session = Depends(get_db)):
    enqueue(db, "pairwise_round", {"window_days": window_days, "max_pairs": max_pairs}, max_attempts=1)
    audit(db, "pairwise.start", detail=str(window_days))
    return RedirectResponse("/admin/improve", status_code=303)


# ----------------------------------------------------------------------------- authors, jobs
@router.get("/authors", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def authors(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Author).order_by(Author.id.desc()).all()
    return page(request, "admin/authors.html", authors=rows)


@router.post("/authors/{slug}/action", dependencies=[Depends(require_admin)])
def author_action(slug: str, action: str = Form(...), reason: str = Form(""), db: Session = Depends(get_db)):
    a = db.query(Author).filter(Author.slug == slug).first()
    if a:
        if action == "suspend":
            a.suspended, a.suspended_reason = True, reason or "misconduct"
        elif action == "unsuspend":
            a.suspended, a.suspended_reason = False, ""
        elif action == "exempt":
            a.rate_limit_exempt = not a.rate_limit_exempt
        audit(db, f"author.{action}", slug, reason=reason)
    return RedirectResponse("/admin/authors", status_code=303)


@router.get("/jobs", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def jobs(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Job).order_by(Job.id.desc()).limit(200).all()
    return page(request, "admin/jobs.html", jobs=rows)


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(require_admin)])
def job_retry(job_id: int, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if j and j.status == "failed":
        j.status, j.attempts, j.run_after = "queued", 0, utcnow()
    return RedirectResponse("/admin/jobs", status_code=303)
