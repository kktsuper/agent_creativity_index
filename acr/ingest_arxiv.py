"""Ingest recent arXiv papers as benchmark submissions.

Each paper becomes an ACR submission whose author is the paper's author list, registered once as a "human agents"
author. Priority date = arXiv submission date. The abstract is stored and the paper links to arXiv; the full
text is fetched at review time and never stored. No rebuttal stage. The paper is excluded from its own prior art.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import time
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from .anchor import sha256_hex
from .auth import hash_key, key_prefix, new_api_key
from .ids import next_acr_id
from .jobs import enqueue
from .models import Anchor, Author, Job, Paper

UA = {"User-Agent": "ACR arXiv ingest (https://acr.example) acr/0.1"}
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# ten fields, ten arXiv primary categories
DEFAULT_FIELDS = {
    "machine learning": "cs.LG",
    "computation and language": "cs.CL",
    "cryptography and security": "cs.CR",
    "combinatorics": "math.CO",
    "probability": "math.PR",
    "quantum physics": "quant-ph",
    "astrophysics": "astro-ph.GA",
    "materials science": "cond-mat.mtrl-sci",
    "neuroscience": "q-bio.NC",
    "economic theory": "econ.TH",
}


def _get(url: str, tries: int = 4) -> httpx.Response:
    for i in range(tries):
        r = httpx.get(url, headers=UA, timeout=90, follow_redirects=True)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(4 * (i + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def parse_feed(xml_text: str) -> list[dict]:
    out = []
    for e in ET.fromstring(xml_text).findall("a:entry", NS):
        aid = (e.findtext("a:id", default="", namespaces=NS) or "").rsplit("/abs/", 1)[-1]
        base_id = re.sub(r"v\d+$", "", aid)
        published = e.findtext("a:published", default="", namespaces=NS)
        cats = [c.get("term") for c in e.findall("a:category", NS)]
        prim = e.find("arxiv:primary_category", NS)
        out.append({
            "arxiv_id": base_id, "version_id": aid,
            "title": " ".join((e.findtext("a:title", default="", namespaces=NS)).split()),
            "abstract": " ".join((e.findtext("a:summary", default="", namespaces=NS)).split()),
            "authors": [a.findtext("a:name", default="", namespaces=NS) for a in e.findall("a:author", NS)],
            "published": dt.datetime.strptime(published[:19], "%Y-%m-%dT%H:%M:%S") if published else None,
            "primary_category": prim.get("term") if prim is not None else (cats[0] if cats else ""),
            "categories": cats, "url": f"https://arxiv.org/abs/{base_id}",
        })
    return out


def fetch_recent(category: str, days: int = 7, max_results: int = 25) -> list[dict]:
    """Newest first-version papers whose primary category is `category`. Uses the arXiv API; when that host is
    throttling (429/503/timeouts), falls back to the listing feed on rss.arxiv.org, which is served separately."""
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    try:
        url = (f"https://export.arxiv.org/api/query?search_query=cat:{category}&sortBy=submittedDate&sortOrder=descending"
               f"&max_results={max_results}")
        entries = [x for x in parse_feed(_get(url).text) if x["published"] and x["published"] >= since]
        return [x for x in entries if x["primary_category"] == category and x["version_id"].endswith("v1")]
    except Exception as api_err:
        entries = fetch_listing_feed(category)
        if not entries:
            raise api_err
        return [x for x in entries if x["published"] and x["published"] >= since]


def fetch_listing_feed(category: str) -> list[dict]:
    """Parse https://rss.arxiv.org/atom/<category>: today's announcements. Only 'new' papers (not replacements
    or cross-lists) with this primary category. The feed's <published> is the announcement time; the exact
    submission timestamp is fetched from the API per paper in ingest_entry when possible."""
    r = httpx.get(f"https://rss.arxiv.org/atom/{category}", headers=UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        if (e.findtext("arxiv:announce_type", default="", namespaces=ns) or "").strip() != "new":
            continue
        link = e.findtext("a:id", default="", namespaces=ns) or ""
        m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", link)
        if not m:
            continue
        cats = [c.get("term") for c in e.findall("a:category", ns)]
        if cats and cats[0] != category:
            continue
        summary = " ".join((e.findtext("a:summary", default="", namespaces=ns)).split())
        summary = re.sub(r"^arXiv:\S+ Announce Type: \w+\s*Abstract:\s*", "", summary)
        authors = [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)]
        if not authors:
            creator = e.findtext("{http://purl.org/dc/elements/1.1/}creator", default="") or ""
            authors = [x.strip() for x in re.split(r",\s*(?![^()]*\))", creator) if x.strip()]
        pub = e.findtext("a:published", default="", namespaces=ns) or e.findtext("a:updated", default="", namespaces=ns)
        out.append({"arxiv_id": m.group(1), "version_id": m.group(1) + (m.group(2) or "v1"),
                    "title": " ".join((e.findtext("a:title", default="", namespaces=ns)).split()),
                    "abstract": summary, "authors": authors,
                    "published": dt.datetime.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S") if pub else None,
                    "date_source": "arXiv listing feed (announcement time)",
                    "primary_category": cats[0] if cats else category, "categories": cats,
                    "url": f"https://arxiv.org/abs/{m.group(1)}"})
    return out


def fetch_fulltext(arxiv_id: str, max_chars: int = 150000) -> str:
    """Download the PDF and extract text (in memory only)."""
    from pypdf import PdfReader
    r = _get(f"https://arxiv.org/pdf/{arxiv_id}")
    reader = PdfReader(io.BytesIO(r.content))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return text[:max_chars]


def _author_slug(arxiv_id: str) -> str:
    return "arxiv-" + arxiv_id.replace(".", "-").replace("/", "-")


def ensure_author(db: Session, entry: dict) -> Author:
    slug = _author_slug(entry["arxiv_id"])
    a = db.query(Author).filter(Author.slug == slug).first()
    if a:
        return a
    names = entry["authors"]
    display = ", ".join(names[:3]) + (f" et al. ({len(names)} authors)" if len(names) > 3 else "")
    key = new_api_key()   # generated so the row is well-formed; never handed out
    a = Author(slug=slug, name=display, kind="human", lab="human",
               description=f"Human authors of arXiv:{entry['arxiv_id']}, ingested by ACR as a benchmark submission. "
                           f"Authors: {', '.join(names)}.",
               base_models=[], homepage=entry["url"], developer_contact="arxiv-ingest",
               api_key_hash=hash_key(key), api_key_prefix=key_prefix(key), rate_limit_exempt=True)
    db.add(a)
    db.flush()
    return a


def ingest_entry(db: Session, entry: dict, field: str) -> Paper:
    existing = db.query(Paper).filter(Paper.external_id == entry["arxiv_id"]).first()
    if existing:
        return existing
    date_note = ""
    if entry.get("date_source"):   # listing-feed entry: try to get the exact submission timestamp from the API
        try:
            from .priorart import _get as _throttled_get
            exact = parse_feed(_throttled_get(f"https://export.arxiv.org/api/query?id_list={entry['arxiv_id']}", UA).text)
            if exact and exact[0]["published"]:
                entry = dict(entry, published=exact[0]["published"], abstract=exact[0]["abstract"] or entry["abstract"],
                             authors=exact[0]["authors"] or entry["authors"], categories=exact[0]["categories"] or entry["categories"])
            else:
                date_note = f"; priority date from the {entry['date_source']}"
        except Exception:
            date_note = f"; priority date from the {entry['date_source']}"
    author = ensure_author(db, entry)
    manifest = f"arxiv:{entry['arxiv_id']}|{entry['title']}|{entry['abstract']}".encode()
    acr_id = next_acr_id(db, entry["published"])
    p = Paper(acr_id=acr_id, author_id=author.id, title=entry["title"], abstract=entry["abstract"], paper_type="result",
              field=field, keywords=[c for c in entry["categories"]][:8], format="arxiv", source_kind="arxiv",
              external_id=entry["arxiv_id"], external_url=entry["url"], skip_rebuttal=True, body_md="", body_blob_key="",
              content_hash=sha256_hex(manifest), word_count=len(entry["abstract"].split()),
              priority_at=entry["published"], embargo_months=0, embargo_until=entry["published"], status="received",
              status_reason="ingested from arXiv" + date_note)
    db.add(p)
    db.flush()
    db.add(Anchor(paper_id=p.id, backend="arxiv", calendar_url=entry["url"], digest_hex=p.content_hash, status="anchored",
                  detail=f"Priority date is the arXiv submission timestamp {entry['published'].isoformat()}Z; ACR did not anchor this hash."))
    enqueue(db, "desk_screen", {"paper_id": p.id})
    return p


def ingest_recent(db: Session, fields: dict[str, str] | None = None, days: int = 7, per_field: int = 1,
                  pick: str = "latest", sleep_s: float = 3.5, exclude_ids: set[str] | None = None) -> list[dict]:
    """Pull `per_field` new papers from each category. arXiv asks for >= 3 s between API calls."""
    fields = fields or DEFAULT_FIELDS
    exclude_ids = {x.strip() for x in (exclude_ids or set())}
    report = []
    for field, cat in fields.items():
        try:
            entries = fetch_recent(cat, days=days)
        except Exception as e:
            report.append({"field": field, "category": cat, "error": f"{type(e).__name__}: {e}"})
            time.sleep(sleep_s)
            continue
        entries = [x for x in entries if x["arxiv_id"] not in exclude_ids]
        chosen = entries[:per_field] if pick == "latest" else entries[len(entries) // 2: len(entries) // 2 + per_field]
        for entry in chosen:
            p = ingest_entry(db, entry, field)
            db.commit()
            report.append({"field": field, "category": cat, "acr_id": p.acr_id, "arxiv_id": entry["arxiv_id"],
                           "title": entry["title"], "published": entry["published"].isoformat()})
        if not chosen:
            report.append({"field": field, "category": cat, "error": f"no new v1 papers in the last {days} days"})
        time.sleep(sleep_s)
    return report
