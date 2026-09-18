"""Submission intake: canonical hashing, PDF text extraction, desk screen (format & completeness only)."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re

from sqlalchemy.orm import Session

from .anchor import sha256_hex
from .config import get_settings
from .ids import next_acr_id
from .models import Anchor, Attachment, Author, Job, Paper
from .storage import get_storage
from .models import utcnow

FIELDS_HINT = "any field, free text (e.g. 'machine learning', 'number theory', 'materials', 'economics')"
PAPER_TYPES = ("proposal", "result")
MAX_EMBARGO_MONTHS = 12


class SubmissionError(ValueError):
    pass


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def canonical_hash(title: str, abstract: str, body_bytes: bytes, attachments: list[tuple[str, bytes]]) -> str:
    """sha256 over a canonical JSON manifest of the submission, so the anchored digest covers everything."""
    manifest = {
        "title": title.strip(), "abstract": abstract.strip(),
        "body_sha256": sha256_hex(body_bytes),
        "attachments": sorted([{"name": n, "sha256": sha256_hex(b)} for n, b in attachments], key=lambda x: x["name"]),
    }
    return sha256_hex(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def desk_screen(paper: Paper) -> tuple[bool, str]:
    """Format and completeness only. No judgment of quality."""
    problems = []
    if len(paper.title.strip()) < 8:
        problems.append("title shorter than 8 characters")
    if len(paper.abstract.split()) < 40:
        problems.append("abstract shorter than 40 words")
    if paper.word_count < 400:
        problems.append(f"body has {paper.word_count} words; minimum 400")
    if paper.paper_type not in PAPER_TYPES:
        problems.append("type must be proposal or result")
    if not paper.field.strip():
        problems.append("field is empty")
    body = paper.body_md or ""
    if paper.format == "pdf" and len(body.strip()) < 500:
        problems.append("PDF has no extractable text (scanned image?)")
    headings = len(re.findall(r"^#{1,3}\s+\S", body, re.M))
    if paper.format == "md" and headings < 2:
        problems.append("markdown paper needs at least two section headings")
    if not any(k in body.lower() for k in ("reference", "related work", "prior", "bibliograph", "citation")):
        problems.append("no references / related-work section found")
    return (len(problems) == 0, "; ".join(problems))


def create_submission(db: Session, author: Author, *, title: str, abstract: str, paper_type: str, field: str,
                      keywords: list[str], fmt: str, body: bytes, attachments: list[dict],
                      embargo_months: int, related: list[str]) -> Paper:
    s = get_settings()
    if fmt not in ("md", "pdf"):
        raise SubmissionError("format must be md or pdf")
    if len(body) > s.max_paper_bytes:
        raise SubmissionError(f"paper exceeds {s.max_paper_bytes} bytes")
    if not (0 <= embargo_months <= MAX_EMBARGO_MONTHS):
        raise SubmissionError(f"embargo_months must be between 0 and {MAX_EMBARGO_MONTHS}")
    if len(attachments) > s.max_attachments:
        raise SubmissionError(f"at most {s.max_attachments} attachments")
    for a in attachments:
        if len(a["data"]) > s.max_attachment_bytes:
            raise SubmissionError(f"attachment {a['filename']} exceeds {s.max_attachment_bytes} bytes")
        if a["kind"] not in ("code", "data"):
            raise SubmissionError("attachment kind must be code or data")

    now = utcnow()
    if fmt == "pdf":
        try:
            text = extract_pdf_text(body)
        except Exception as e:
            raise SubmissionError(f"could not parse PDF: {e}")
    else:
        text = body.decode("utf-8", "replace")

    chash = canonical_hash(title, abstract, body, [(a["filename"], a["data"]) for a in attachments])
    acr_id = next_acr_id(db, now)
    st = get_storage()
    body_key = f"papers/{acr_id}/paper.{fmt}"
    st.put(body_key, body, "application/pdf" if fmt == "pdf" else "text/markdown")

    paper = Paper(acr_id=acr_id, author_id=author.id, title=title.strip(), abstract=abstract.strip(),
                  paper_type=paper_type, field=field.strip().lower(), keywords=[k.strip() for k in keywords if k.strip()][:12],
                  format=fmt, body_md=text, body_blob_key=body_key, content_hash=chash,
                  word_count=len(text.split()), priority_at=now, embargo_months=embargo_months,
                  embargo_until=_add_months(now, embargo_months), status="received",
                  author_declared_related=[r for r in related if re.fullmatch(r"ACR-\d{4}-\d{6}", r)][:50])
    db.add(paper)
    db.flush()
    for a in attachments:
        key = f"papers/{acr_id}/attachments/{a['kind']}/{a['filename']}"
        st.put(key, a["data"], a.get("content_type") or "application/octet-stream")
        db.add(Attachment(paper_id=paper.id, kind=a["kind"], filename=a["filename"],
                          content_type=a.get("content_type") or "application/octet-stream",
                          size_bytes=len(a["data"]), sha256=sha256_hex(a["data"]), blob_key=key))
    db.add(Anchor(paper_id=paper.id, backend="pending", digest_hex=chash, status="pending", detail="queued"))
    db.add(Job(kind="anchor", payload={"paper_id": paper.id}))
    db.add(Job(kind="desk_screen", payload={"paper_id": paper.id}))
    db.flush()
    return paper


def _add_months(d: dt.datetime, months: int) -> dt.datetime:
    if months <= 0:
        return d
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)
