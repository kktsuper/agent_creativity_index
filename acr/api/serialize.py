from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Author, Paper, Review, ReviewRun, Transcript


def iso(d):
    return d.isoformat() + "Z" if d else None


def author_json(a: Author, papers: int | None = None) -> dict:
    d = {"slug": a.slug, "name": a.name, "kind": a.kind, "description": a.description, "lab": a.lab, "base_models": a.base_models,
         "homepage": a.homepage, "created_at": iso(a.created_at), "url": f"{get_settings().base_url}/authors/{a.slug}"}
    if papers is not None:
        d["public_papers"] = papers
    return d


def paper_summary(p: Paper) -> dict:
    return {"acr_id": p.acr_id, "title": p.title, "type": p.paper_type, "field": p.field, "keywords": p.keywords,
            "author": {"slug": p.author.slug, "name": p.author.name, "lab": p.author.lab, "kind": p.author.kind},
            "source": {"kind": p.source_kind, "external_id": p.external_id or None, "external_url": p.external_url or None},
            "priority_at": iso(p.priority_at), "public_at": iso(p.public_at), "decision": p.decision,
            "status": p.status, "scores": scores_json(p), "harness_version": p.harness_version,
            "url": f"{get_settings().base_url}/papers/{p.acr_id}"}


def scores_json(p: Paper) -> dict | None:
    if p.score_creativity is None:
        return None
    return {"originality": p.score_originality, "depth": p.score_depth, "potential_impact": p.score_potential_impact,
            "implementation": p.score_implementation, "novelty": p.score_novelty, "impact": p.score_impact,
            "creativity": p.score_creativity}


def review_json(r: Review) -> dict:
    return {"slot": r.slot, "lab": r.lab, "model": r.model, "initial": r.initial, "final": r.final or None}


def run_json(run: ReviewRun, include_transcripts: bool = False) -> dict:
    d = {"id": run.id, "harness_version": run.harness_version, "sandbox": run.sandbox, "stage": run.stage,
         "committee": run.committee, "prior_art": run.prior_art, "decision": run.decision, "meta_review": run.meta_review,
         "final_scores": run.final_scores, "rebuttal": {"opens_at": iso(run.rebuttal_opens_at),
                                                        "deadline": iso(run.rebuttal_deadline),
                                                        "submitted_at": iso(run.rebuttal_submitted_at),
                                                        "text": run.rebuttal_text, "tokens": run.rebuttal_tokens},
         "reviews": [review_json(r) for r in sorted(run.reviews, key=lambda x: x.slot)],
         "tokens": {"input": run.total_input_tokens, "output": run.total_output_tokens}, "cost_usd": run.total_cost_usd,
         "created_at": iso(run.created_at), "finished_at": iso(run.finished_at), "error": run.error or None}
    if include_transcripts:
        d["transcripts"] = [transcript_json(t) for t in sorted(run.transcripts, key=lambda x: x.seq)]
    return d


def transcript_json(t: Transcript) -> dict:
    return {"seq": t.seq, "stage": t.stage, "role": t.role, "lab": t.lab, "model": t.model, "provider": t.provider,
            "harness_version": t.harness_version, "system_prompt": t.system_prompt, "messages": t.messages,
            "response_text": t.response_text, "response_json": t.response_json,
            "input_tokens": t.input_tokens, "output_tokens": t.output_tokens, "latency_ms": t.latency_ms,
            "note": t.note, "created_at": iso(t.created_at)}


def paper_full(db: Session, p: Paper, public_view: bool = True) -> dict:
    from ..citations import cites, cited_by
    d = paper_summary(p)
    d.update({"abstract": p.abstract, "format": p.format, "word_count": p.word_count, "content_hash": p.content_hash,
              "embargo_months": p.embargo_months, "embargo_until": iso(p.embargo_until), "decided_at": iso(p.decided_at),
              "status_reason": p.status_reason,
              "attachments": [{"kind": a.kind, "filename": a.filename, "size_bytes": a.size_bytes, "sha256": a.sha256}
                              for a in p.attachments],
              "anchors": [{"backend": a.backend, "calendar": a.calendar_url, "status": a.status, "detail": a.detail,
                           "created_at": iso(a.created_at)} for a in p.anchors]})
    if p.public_at or not public_view:
        d["body_md"] = p.body_md if p.source_kind != "arxiv" else None
        run = db.get(ReviewRun, p.official_run_id) if p.official_run_id else None
        d["review"] = run_json(run, include_transcripts=True) if run else None
        d["cites"] = [{"acr_id": c["acr_id"], "public": c["paper"] is not None,
                       "title": c["paper"].title if c["paper"] else None} for c in cites(db, p)]
        d["cited_by"] = [{"acr_id": q.acr_id, "title": q.title} for q in cited_by(db, p)]
    return d
