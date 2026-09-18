"""Background jobs. A single worker loop (thread in dev, dedicated Cloud Run service in prod) polls the
jobs table. Every job is idempotent and retry-safe. tick() runs once a minute for time-based transitions."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import traceback

from sqlalchemy.orm import Session

from .anchor import anchor_digest
from .citations import reindex_paper
from .config import get_settings
from .db import db_session
from .harness.defaults import current_harness
from .harness.runner import Engine, create_run
from .models import Anchor, HarnessVersion, Job, Paper, ReviewRun, AuditLog
from .settings_store import get_setting
from .submissions import desk_screen
from .models import utcnow

log = logging.getLogger("acr.jobs")


REVIEW_MAX_ATTEMPTS = 1000   # review stages retry until they resolve; only the spend cap is terminal


def enqueue(db: Session, kind: str, payload: dict | None = None, delay_seconds: float = 0, max_attempts: int = 3) -> Job:
    if kind == "advance_run":
        max_attempts = max(max_attempts, REVIEW_MAX_ATTEMPTS)
    j = Job(kind=kind, payload=payload or {}, run_after=utcnow() + dt.timedelta(seconds=delay_seconds),
            max_attempts=max_attempts)
    db.add(j)
    db.flush()
    return j


# ----------------------------------------------------------------------------- handlers

def h_desk_screen(db: Session, payload: dict):
    p = db.get(Paper, payload["paper_id"])
    if p is None or p.status != "received":
        return
    if p.source_kind == "arxiv":
        ok, reason = True, "arXiv paper: desk screen not applicable"
    else:
        ok, reason = desk_screen(p)
    if not ok:
        p.status = "desk_rejected"
        p.status_reason = reason
        p.decision = "desk_reject"
        p.decided_at = utcnow()
        return
    p.status_reason = (p.status_reason + "; " + reason) if p.source_kind == "arxiv" else "passed desk screen"
    if get_setting(db, "auto_review", True):
        enqueue(db, "start_review", {"paper_id": p.id})


def h_start_review(db: Session, payload: dict):
    p = db.get(Paper, payload["paper_id"])
    if p is None:
        return
    sandbox = bool(payload.get("sandbox", False))
    if not sandbox and p.official_run_id:
        return   # already has an official run
    hv = db.get(HarnessVersion, payload["harness_version_id"]) if payload.get("harness_version_id") else current_harness(db)
    run = create_run(db, p, hv, sandbox=sandbox)
    enqueue(db, "advance_run", {"run_id": run.id})


def daily_spend_usd(db: Session) -> float:
    from sqlalchemy import func
    from .models import Transcript
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return float(db.query(func.coalesce(func.sum(Transcript.cost_usd), 0.0)).filter(Transcript.created_at >= start).scalar() or 0)


class DailyCapReached(RuntimeError):
    pass


def h_advance_run(db: Session, payload: dict):
    run = db.get(ReviewRun, payload["run_id"])
    if run is None or run.stage in ("done", "failed"):
        return
    cap = float(get_setting(db, "spend_cap_daily_usd", 30.0) or 0)
    if cap > 0 and run.stage not in ("decision",) and daily_spend_usd(db) >= cap:
        raise DailyCapReached(f"daily spend cap ${cap:.2f} reached; run {run.id} paused")
    eng = Engine(db, run)
    if run.stage == "rebuttal":
        if eng.rebuttal_open():
            return   # tick() re-enqueues when the deadline passes or a rebuttal arrives
    new_stage = eng.advance()
    if new_stage == "done":
        p = db.get(Paper, run.paper_id)
        if not run.sandbox and p.official_run_id == run.id:
            if (p.decision == "accept" or p.source_kind == "arxiv") and p.embargo_until <= utcnow():
                enqueue(db, "publish", {"paper_id": p.id})
        return
    if new_stage == "rebuttal" and eng.rebuttal_open():
        return
    enqueue(db, "advance_run", {"run_id": run.id})


def h_publish(db: Session, payload: dict):
    p = db.get(Paper, payload["paper_id"])
    if p is None or p.status != "decided" or p.public_at is not None:
        return
    if p.decision != "accept" and p.source_kind != "arxiv":
        return   # rejected submissions stay private; arXiv benchmark papers are already public and publish with their score
    if p.embargo_until > utcnow():
        return
    if not get_setting(db, "auto_publish", True) and not payload.get("force"):
        return
    p.public_at = utcnow()
    p.status = "published"
    reindex_paper(db, p)


def h_anchor(db: Session, payload: dict):
    p = db.get(Paper, payload["paper_id"])
    if p is None:
        return
    pending = [a for a in p.anchors if a.status in ("pending",) and a.backend == "pending"]
    records = anchor_digest(p.content_hash)
    for a in pending:
        db.delete(a)
    any_ok = False
    for r in records:
        db.add(Anchor(paper_id=p.id, backend=r["backend"], calendar_url=r["calendar_url"], digest_hex=p.content_hash,
                      proof_b64=r["proof_b64"], status=r["status"], detail=r["detail"]))
        any_ok = any_ok or r["status"] in ("pending", "anchored")
    if not any_ok:
        raise RuntimeError("no calendar accepted the digest: " + "; ".join(r["detail"] for r in records))


def h_reindex_citations(db: Session, payload: dict):
    p = db.get(Paper, payload["paper_id"])
    if p:
        reindex_paper(db, p)


def h_pairwise_round(db: Session, payload: dict):
    from .phase3 import run_pairwise_round
    run_pairwise_round(db, int(payload.get("window_days", 90)), int(payload.get("max_pairs", 20)))


def h_harness_improve(db: Session, payload: dict):
    from .phase3 import run_improvement
    run_improvement(db, int(payload["report_id"]))


def rereview_paper(db: Session, paper: Paper, reason: str = "re-review") -> None:
    """Take a paper back to 'received' and start a fresh official run. Unpublishes it and clears its scores;
    earlier runs stay in the database (admin-visible) but are no longer the paper's record. Use when a run was
    made with a degraded committee (e.g. a mocked judge) and the venue wants the real committee's verdict."""
    paper.official_run_id = None
    paper.status, paper.status_reason = "received", reason
    paper.decision = paper.decided_at = paper.public_at = None
    paper.harness_version = None
    for f in ("originality", "depth", "potential_impact", "implementation", "novelty", "impact", "creativity"):
        setattr(paper, f"score_{f}", None)
    from .models import Citation
    db.query(Citation).filter(Citation.from_paper_id == paper.id).delete()
    db.flush()
    enqueue(db, "start_review", {"paper_id": paper.id})


HANDLERS = {
    "desk_screen": h_desk_screen,
    "start_review": h_start_review,
    "advance_run": h_advance_run,
    "publish": h_publish,
    "anchor": h_anchor,
    "reindex_citations": h_reindex_citations,
    "pairwise_round": h_pairwise_round,
    "harness_improve": h_harness_improve,
}


# ----------------------------------------------------------------------------- scheduler

def tick(db: Session) -> None:
    now = utcnow()
    # rebuttal deadlines passed or rebuttal submitted -> continue
    for run in db.query(ReviewRun).filter(ReviewRun.stage == "rebuttal").all():
        if run.rebuttal_submitted_at is not None or (run.rebuttal_deadline and run.rebuttal_deadline <= now):
            if not db.query(Job).filter(Job.kind == "advance_run", Job.status.in_(["queued", "running"]),
                                        Job.payload["run_id"].as_string() == str(run.id)).first():
                enqueue(db, "advance_run", {"run_id": run.id})
    # embargo lifts
    for p in db.query(Paper).filter(Paper.status == "decided", Paper.public_at.is_(None), Paper.embargo_until <= now).all():
        enqueue(db, "publish", {"paper_id": p.id})
    # anchors that failed entirely: retry hourly
    for p in db.query(Paper).filter(Paper.created_at >= now - dt.timedelta(days=7), Paper.source_kind == "submission").all():
        if p.anchors and all(a.status == "failed" for a in p.anchors) and \
                max(a.created_at for a in p.anchors) < now - dt.timedelta(hours=1):
            for a in p.anchors:
                db.delete(a)
            db.add(Anchor(paper_id=p.id, backend="pending", digest_hex=p.content_hash, status="pending", detail="retry"))
            enqueue(db, "anchor", {"paper_id": p.id})
    # stuck 'running' jobs (worker died): requeue after 30 min
    for j in db.query(Job).filter(Job.status == "running", Job.started_at < now - dt.timedelta(minutes=30)).all():
        j.status = "queued"


def _pick_job(db: Session) -> Job | None:
    now = utcnow()
    q = db.query(Job).filter(Job.status == "queued", Job.run_after <= now).order_by(Job.id)
    if db.bind.dialect.name != "sqlite":
        q = q.with_for_update(skip_locked=True)
    j = q.first()
    if j:
        j.status = "running"
        j.started_at = now
        j.attempts += 1
        db.commit()   # release the write lock: a job can spend minutes in model calls (matters on SQLite)
        j = db.get(Job, j.id)
    return j


def run_one(db: Session, job: Job) -> None:
    try:
        HANDLERS[job.kind](db, job.payload)
        job.status = "done"
        job.finished_at = utcnow()
    except DailyCapReached as e:
        db.rollback()
        job = db.get(Job, job.id)
        job.status, job.attempts = "queued", max(0, job.attempts - 1)   # pause, do not count as a failure
        job.run_after = utcnow() + dt.timedelta(hours=1)
        job.last_error = str(e)
        log.warning("%s", e)
    except Exception as e:
        db.rollback()
        job = db.get(Job, job.id)
        job.last_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}"
        from .harness.runner import SpendCapExceeded
        if isinstance(e, SpendCapExceeded):
            job.attempts = job.max_attempts   # a cap is a stop, not a transient error
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = utcnow()
            if job.kind == "advance_run":
                run = db.get(ReviewRun, job.payload["run_id"])
                if run and run.stage not in ("done",):
                    run.stage = "failed"
                    run.error = job.last_error[:2000]
                    p = db.get(Paper, run.paper_id)
                    if p and not run.sandbox and p.official_run_id == run.id:
                        p.status = "in_review"
                        p.status_reason = "review run failed; admin can retry"
        else:
            job.status = "queued"
            job.run_after = utcnow() + dt.timedelta(seconds=min(600, 30 * job.attempts))
        log.error("job %s %s failed (attempt %s): %s", job.id, job.kind, job.attempts, e)


def process_available(max_jobs: int = 1000) -> int:
    """Drain the queue synchronously (used by tests, CLI and the dev worker)."""
    n = 0
    while n < max_jobs:
        with db_session() as db:
            job = _pick_job(db)
            if job is None:
                return n
            run_one(db, job)
        n += 1
    return n


def worker_loop(stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    s = get_settings()
    last_tick = 0.0
    log.info("worker started")
    while not stop.is_set():
        try:
            if time.time() - last_tick > 60:
                with db_session() as db:
                    tick(db)
                last_tick = time.time()
            if process_available(max_jobs=50) == 0:
                stop.wait(s.worker_poll_seconds)
        except Exception:
            log.exception("worker loop error")
            stop.wait(5)


def start_background_worker() -> threading.Event:
    stop = threading.Event()
    t = threading.Thread(target=worker_loop, args=(stop,), daemon=True, name="acr-worker")
    t.start()
    return stop
