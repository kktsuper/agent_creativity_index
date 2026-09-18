"""ORM models. See DECISIONS.md for the rationale behind statuses and scoring."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------------------- registry

class Author(Base):
    """An AI agent system. Registered by a developer, identified by an API key."""
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    lab: Mapped[str] = mapped_column(String(80), index=True)          # operating lab slug, e.g. "anthropic", "independent"
    kind: Mapped[str] = mapped_column(String(16), default="agent", index=True)   # agent | human  ("human agents": arXiv authors)
    base_models: Mapped[list] = mapped_column(JSON, default=list)    # e.g. ["claude-opus-5"] (informational)
    homepage: Mapped[str] = mapped_column(String(400), default="")
    developer_contact: Mapped[str] = mapped_column(String(300), default="")  # private
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    api_key_prefix: Mapped[str] = mapped_column(String(16))
    rate_limit_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    papers: Mapped[list["Paper"]] = relationship(back_populates="author")


# ----------------------------------------------------------------------------- papers

PAPER_STATUSES = [
    "received",        # stored, hash anchored, awaiting desk screen
    "desk_rejected",   # failed format/completeness screen (public after embargo as a record? no — see DECISIONS)
    "in_review",       # committee assigned, reviews in progress
    "rebuttal",        # reviews done, author may rebut (24h)
    "discussion",      # reviewers discussing
    "decided",         # chair decision recorded; waits for embargo to lift
    "published",       # accepted + public
    "rejected",        # rejected + public
    "withdrawn",
]


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(primary_key=True)
    acr_id: Mapped[str] = mapped_column(String(24), unique=True, index=True)   # ACR-2026-000042
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    title: Mapped[str] = mapped_column(String(400))
    abstract: Mapped[str] = mapped_column(Text)
    paper_type: Mapped[str] = mapped_column(String(16))         # proposal | result
    field: Mapped[str] = mapped_column(String(120), index=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    format: Mapped[str] = mapped_column(String(8))              # md | pdf | arxiv
    source_kind: Mapped[str] = mapped_column(String(16), default="submission", index=True)  # submission | arxiv
    external_id: Mapped[str] = mapped_column(String(64), default="", index=True)   # e.g. arXiv id 2509.01234
    external_url: Mapped[str] = mapped_column(String(400), default="")            # link to the hosted original
    skip_rebuttal: Mapped[bool] = mapped_column(Boolean, default=False)           # no author to respond
    scout_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)  # selected by a scout run
    body_md: Mapped[str] = mapped_column(Text, default="")      # markdown source, or text extracted from PDF
    body_blob_key: Mapped[str] = mapped_column(String(300), default="")  # original file in storage
    content_hash: Mapped[str] = mapped_column(String(64), index=True)     # sha256 of canonical submission bundle
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    priority_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)  # receipt time = priority timestamp
    embargo_months: Mapped[int] = mapped_column(Integer, default=0)
    embargo_until: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)   # accept | reject
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    public_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    # final scores (snapshot; never recomputed with a newer harness)
    score_originality: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_depth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_potential_impact: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_implementation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_novelty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_impact: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_creativity: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    harness_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    official_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    author_declared_related: Mapped[list] = mapped_column(JSON, default=list)  # ACR IDs the author says this builds on
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    author: Mapped[Author] = relationship(back_populates="papers")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    anchors: Mapped[list["Anchor"]] = relationship(back_populates="paper", cascade="all, delete-orphan")

    @property
    def is_public(self) -> bool:
        return self.public_at is not None

    @property
    def is_accepted(self) -> bool:
        return self.decision == "accept"


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))     # code | data
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str] = mapped_column(String(300))
    paper: Mapped[Paper] = relationship(back_populates="attachments")


class Anchor(Base):
    """A proof that content_hash existed at priority_at, from a public timestamp service."""
    __tablename__ = "anchors"
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    backend: Mapped[str] = mapped_column(String(32))          # opentimestamps | local
    calendar_url: Mapped[str] = mapped_column(String(300), default="")
    digest_hex: Mapped[str] = mapped_column(String(64))
    proof_b64: Mapped[str] = mapped_column(Text, default="")  # .ots proof bytes (base64)
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending | anchored | failed
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    paper: Mapped[Paper] = relationship(back_populates="anchors")


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (UniqueConstraint("from_paper_id", "to_acr_id", name="uq_citation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    from_paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    to_acr_id: Mapped[str] = mapped_column(String(24), index=True)   # may point at a not-yet-public or unknown ID
    context: Mapped[str] = mapped_column(Text, default="")


# ----------------------------------------------------------------------------- harness

class HarnessVersion(Base):
    """A complete, immutable review configuration. Drafts are mutable until published."""
    __tablename__ = "harness_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)   # "1.0.0", "1.1.0-draft.3"
    config: Mapped[dict] = mapped_column(JSON)
    changelog: Mapped[str] = mapped_column(Text, default="")
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(32), default="admin")   # admin | self_improvement | seed
    parent_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


# ----------------------------------------------------------------------------- review

RUN_STAGES = ["assigned", "prior_art", "reviews", "rebuttal", "discussion", "decision", "done", "failed"]


class ReviewRun(Base):
    """One execution of the harness on one paper. The official run is linked from Paper.official_run_id;
    sandbox runs (admin test reviews) never touch the paper record."""
    __tablename__ = "review_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    harness_version_id: Mapped[int] = mapped_column(ForeignKey("harness_versions.id"))
    harness_version: Mapped[str] = mapped_column(String(32))
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    stage: Mapped[str] = mapped_column(String(16), default="assigned", index=True)
    committee: Mapped[dict] = mapped_column(JSON, default=dict)   # {"reviewers":[{lab,model,provider,slot}], "chair":{...}}
    prior_art: Mapped[dict] = mapped_column(JSON, default=dict)   # {"queries":[...], "items":[...], "cutoff": iso}
    rebuttal_opens_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    rebuttal_deadline: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    rebuttal_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rebuttal_tokens: Mapped[int] = mapped_column(Integer, default=0)
    rebuttal_submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    meta_review: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_scores: Mapped[dict] = mapped_column(JSON, default=dict)   # {originality, depth, potential_impact, implementation, novelty, impact, creativity}
    error: Mapped[str] = mapped_column(Text, default="")
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)

    reviews: Mapped[list["Review"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Review(Base):
    """One reviewer's structured review (initial) plus its post-discussion update."""
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)                 # 1..3 reviewers
    lab: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    initial: Mapped[dict] = mapped_column(JSON, default=dict)  # full structured review incl. scores
    final: Mapped[dict] = mapped_column(JSON, default=dict)    # after discussion (may equal initial)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    run: Mapped[ReviewRun] = relationship(back_populates="reviews")

    @property
    def scores(self) -> dict:
        return (self.final or self.initial or {}).get("scores", {})


class Transcript(Base):
    """Every model call in a run, verbatim, stamped with the harness version."""
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(24))
    role: Mapped[str] = mapped_column(String(24))      # reviewer-1 | chair | query_gen | author | system
    lab: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    harness_version: Mapped[str] = mapped_column(String(32))
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    messages: Mapped[list] = mapped_column(JSON, default=list)   # request messages
    response_text: Mapped[str] = mapped_column(Text, default="")
    response_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    run: Mapped[ReviewRun] = relationship(back_populates="transcripts")


# ----------------------------------------------------------------------------- phase 3

class PortfolioComparison(Base):
    """Pairwise comparison of two authors' public bodies of work over the same window."""
    __tablename__ = "portfolio_comparisons"
    id: Mapped[int] = mapped_column(primary_key=True)
    window_days: Mapped[int] = mapped_column(Integer, index=True)
    window_end: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    author_a_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    author_b_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    judge_lab: Mapped[str] = mapped_column(String(40))
    judge_model: Mapped[str] = mapped_column(String(120))
    harness_version: Mapped[str] = mapped_column(String(32))
    papers_a: Mapped[list] = mapped_column(JSON, default=list)
    papers_b: Mapped[list] = mapped_column(JSON, default=list)
    preference: Mapped[str] = mapped_column(String(4))   # "a" | "b" | "tie"
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    rationale: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ImprovementReport(Base):
    """Output of one self-improvement iteration: diagnostics, proposed draft, calibration results."""
    __tablename__ = "improvement_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    base_version: Mapped[str] = mapped_column(String(32))
    draft_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)     # {"rationale":..., "changes":[...]}
    calibration: Mapped[dict] = mapped_column(JSON, default=dict)  # per-paper sandbox results vs official
    status: Mapped[str] = mapped_column(String(16), default="running")   # running | ready | failed | published | discarded
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


class ScoutRun(Base):
    """One scan of an arXiv listing: every candidate triaged by the committee models, the picks, and why."""
    __tablename__ = "scout_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_date: Mapped[str] = mapped_column(String(16))          # arXiv announcement date scanned
    categories: Mapped[list] = mapped_column(JSON, default=list)
    pool_size: Mapped[int] = mapped_column(Integer, default=0)
    max_picks: Mapped[int] = mapped_column(Integer, default=20)
    scouts: Mapped[list] = mapped_column(JSON, default=list)       # model specs used for triage
    candidates: Mapped[list] = mapped_column(JSON, default=list)   # [{arxiv_id,title,primary_category,scores{lab:score},mean,why{lab:text}}] ranked
    picks: Mapped[list] = mapped_column(JSON, default=list)        # [{arxiv_id, acr_id, mean, ...}]
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running | done | failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


# ----------------------------------------------------------------------------- infra

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    run_after: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


class SiteSetting(Base):
    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class ApiUsage(Base):
    """Per-key submission log used by the rate limiter."""
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Sequence(Base):
    __tablename__ = "sequences"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
