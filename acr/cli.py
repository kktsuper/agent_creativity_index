"""Operational CLI:  python -m acr.cli <command>"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .db import db_session, init_db
from .models import utcnow


def cmd_init_db(_):
    init_db()
    print("database initialized")


def cmd_worker(_):
    init_db()
    from .jobs import worker_loop
    worker_loop()


def cmd_drain(_):
    init_db()
    from .jobs import process_available, tick
    with db_session() as db:
        tick(db)
    print("processed", process_available())


def cmd_review(args):
    """Run a review synchronously (official or sandbox) for a paper."""
    init_db()
    from .harness.runner import Engine, create_run
    from .harness.defaults import current_harness
    from .models import HarnessVersion, Paper
    with db_session() as db:
        p = db.query(Paper).filter(Paper.acr_id == args.acr_id).first()
        if not p:
            sys.exit("unknown paper")
        hv = db.query(HarnessVersion).filter(HarnessVersion.version == args.harness).first() if args.harness else current_harness(db)
        run = create_run(db, p, hv, sandbox=args.sandbox)
        eng = Engine(db, run)
        while run.stage not in ("done", "failed"):
            if run.stage == "rebuttal" and eng.rebuttal_open():
                run.rebuttal_deadline = utcnow()   # CLI runs don't wait
            print("stage", run.stage, "->", eng.advance())
        print(json.dumps({"run": run.id, "decision": run.decision, "scores": run.final_scores}, indent=1))


def cmd_publish_due(_):
    init_db()
    from .jobs import enqueue, process_available
    from .models import Paper
    with db_session() as db:
        for p in db.query(Paper).filter(Paper.status == "decided", Paper.public_at.is_(None),
                                        Paper.embargo_until <= utcnow()).all():
            enqueue(db, "publish", {"paper_id": p.id})
    print("processed", process_available())


def cmd_pairwise(args):
    init_db()
    from .phase3 import run_pairwise_round
    with db_session() as db:
        print("comparisons:", run_pairwise_round(db, args.window, args.max_pairs))


def cmd_improve(_):
    init_db()
    from .models import ImprovementReport
    from .harness.defaults import current_harness
    from .phase3 import run_improvement
    with db_session() as db:
        rep = ImprovementReport(base_version=current_harness(db).version)
        db.add(rep); db.flush()
        rid = rep.id
    with db_session() as db:
        run_improvement(db, rid)
        rep = db.get(ImprovementReport, rid)
        print(json.dumps({"status": rep.status, "draft": rep.draft_version, "calibration": rep.calibration}, indent=1))


def cmd_ingest_arxiv(args):
    init_db()
    from .ingest_arxiv import ingest_recent, DEFAULT_FIELDS
    fields = DEFAULT_FIELDS
    if args.fields:
        fields = dict(x.split("=", 1) for x in args.fields.split(","))
    with db_session() as db:
        rep = ingest_recent(db, fields, days=args.days, per_field=args.per_field, pick=args.pick,
                            exclude_ids=set(args.exclude.split(",")) if args.exclude else None)
    for r in rep:
        print(json.dumps(r))


def cmd_scout(args):
    init_db()
    from .scout import run_scout, AI_ML_CATEGORIES
    cats = AI_ML_CATEGORIES
    if args.categories:
        cats = {c: AI_ML_CATEGORIES.get(c, c) for c in args.categories.split(",")}
    with db_session() as db:
        run = run_scout(db, cats, max_picks=args.max, min_mean=args.min_mean, ingest=not args.dry_run)
        print(json.dumps({"scout_run": run.id, "pool": run.pool_size, "cost_usd": run.cost_usd, "status": run.status,
                          "picks": [{k: c.get(k) for k in ("arxiv_id", "acr_id", "mean", "title")} for c in run.picks]}, indent=1))


def cmd_purge_seed(_):
    """Delete the dev seed authors (slugs from seed/agents.py) and everything attached to them."""
    init_db()
    from .models import Author, Paper, ReviewRun, Review, Transcript, Attachment, Anchor, Citation, Job, ApiUsage, PortfolioComparison
    from seed.agents import AGENTS
    slugs = [a["slug"] for a in AGENTS]
    with db_session() as db:
        authors = db.query(Author).filter(Author.slug.in_(slugs)).all()
        for a in authors:
            for p in db.query(Paper).filter(Paper.author_id == a.id).all():
                for run in db.query(ReviewRun).filter(ReviewRun.paper_id == p.id).all():
                    db.query(Transcript).filter(Transcript.run_id == run.id).delete()
                    db.query(Review).filter(Review.run_id == run.id).delete()
                    db.delete(run)
                db.query(Attachment).filter(Attachment.paper_id == p.id).delete()
                db.query(Anchor).filter(Anchor.paper_id == p.id).delete()
                db.query(Citation).filter(Citation.from_paper_id == p.id).delete()
                db.delete(p)
            db.query(ApiUsage).filter(ApiUsage.author_id == a.id).delete()
            db.query(PortfolioComparison).filter((PortfolioComparison.author_a_id == a.id) | (PortfolioComparison.author_b_id == a.id)).delete()
            db.delete(a)
        print("purged", [a.slug for a in authors])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="acr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sub.add_parser("drain").set_defaults(fn=cmd_drain)
    r = sub.add_parser("review"); r.add_argument("acr_id"); r.add_argument("--harness"); r.add_argument("--sandbox", action="store_true")
    r.set_defaults(fn=cmd_review)
    sub.add_parser("publish-due").set_defaults(fn=cmd_publish_due)
    pw = sub.add_parser("pairwise"); pw.add_argument("--window", type=int, default=90); pw.add_argument("--max-pairs", type=int, default=20)
    pw.set_defaults(fn=cmd_pairwise)
    sub.add_parser("improve").set_defaults(fn=cmd_improve)
    ia = sub.add_parser("ingest-arxiv"); ia.add_argument("--fields", help="field=category,... (default: 10 built-in fields)")
    ia.add_argument("--days", type=int, default=7); ia.add_argument("--per-field", type=int, default=1)
    ia.add_argument("--pick", choices=["latest", "middle"], default="latest"); ia.add_argument("--exclude", default="", help="arXiv ids to skip, comma-separated")
    ia.set_defaults(fn=cmd_ingest_arxiv)
    sub.add_parser("purge-seed").set_defaults(fn=cmd_purge_seed)
    sc = sub.add_parser("scout-arxiv"); sc.add_argument("--categories", default="", help="comma-separated arXiv categories (default: AI/ML set)")
    sc.add_argument("--max", type=int, default=20); sc.add_argument("--min-mean", type=float, default=0.0)
    sc.add_argument("--dry-run", action="store_true", help="triage and record, but do not ingest picks"); sc.set_defaults(fn=cmd_scout)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
