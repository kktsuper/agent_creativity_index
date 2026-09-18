"""Dated prior-art search.

Two passes, merged:
  1. General search by the committee's own models with their native tools (web, patents, products, news, code).
     Each searcher writes a free-text report; a second call extracts structured items with a date and how the
     date was established. Items without a parseable date, or dated on/after the priority timestamp, are dropped.
     Dates for arXiv / DOI URLs are re-checked against the authoritative API and override the model's claim.
  2. Structured dated APIs (ACR's own record, arXiv, Crossref, Semantic Scholar) for academic coverage.

The rule everywhere: only material that existed before the priority date counts. Undated material never counts.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .config import get_settings

UA = {"User-Agent": "ACR prior-art search (https://acr.example) acr/0.1"}

KINDS = ["paper", "patent", "product", "news", "code", "web"]

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {"type": "string", "enum": KINDS},
            "date": {"type": "string", "description": "YYYY-MM-DD, YYYY-MM, or YYYY. Empty string if no date could be established."},
            "date_evidence": {"type": "string", "description": "How the date was established (dateline, filing date, release note, archive snapshot, DOI record)."},
            "url": {"type": "string"},
            "source_name": {"type": "string"},
            "snippet": {"type": "string"},
            "relevance": {"type": "string", "description": "Why this could anticipate or relate to the submission's central contribution."},
        },
        "required": ["title", "kind", "date", "date_evidence", "url", "source_name", "snippet", "relevance"],
        "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


# ----------------------------------------------------------------------------- dates
def latest_date(s: str | None) -> dt.datetime | None:
    """Parse YYYY[-MM[-DD]] (or ISO) to the LATEST instant the string could mean, so a coarse date is
    treated conservatively against the cutoff (a bare '2026' is not before 2026-09-03)."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        return None
    y = int(m.group(1))
    if not (1500 <= y <= 2200):
        return None
    mo = int(m.group(2)) if m.group(2) else None
    d = int(m.group(3)) if m.group(3) else None
    try:
        if mo and d:
            if len(s) > 10 and "T" in s:
                return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt.datetime(y, mo, d, 23, 59, 59)
        if mo:
            return dt.datetime(y, mo, calendar.monthrange(y, mo)[1], 23, 59, 59)
        return dt.datetime(y, 12, 31, 23, 59, 59)
    except ValueError:
        return None


def _parse_date(s: str | None) -> dt.datetime | None:
    return latest_date(s)


# ----------------------------------------------------------------------------- dated APIs
_last_arxiv_call = 0.0


def _get(url: str, headers: dict) -> httpx.Response:
    """GET with redirects and one backoff retry on 429/5xx (public APIs throttle unkeyed clients).
    arXiv calls are spaced at least 3.2 s apart process-wide, as arXiv asks."""
    global _last_arxiv_call
    for attempt in range(2):
        if "arxiv.org" in url:
            wait = _last_arxiv_call + 3.2 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last_arxiv_call = time.time()
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if r.status_code in (429, 500, 502, 503) and attempt == 0:
            ra = r.headers.get("retry-after", "3")
            time.sleep(float(ra) if ra.isdigit() else 3)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def search_arxiv(q: str, cutoff: dt.datetime, n: int) -> list[dict]:
    url = f"https://export.arxiv.org/api/query?search_query=all:{quote_plus(q)}&max_results={n}&sortBy=relevance"
    r = _get(url, UA)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        pub = _parse_date(e.findtext("a:published", default="", namespaces=ns))
        if not pub or pub >= cutoff:
            continue
        out.append({"source": "arxiv", "kind": "paper", "title": " ".join((e.findtext("a:title", default="", namespaces=ns)).split()),
                    "authors": [a.findtext("a:name", default="", namespaces=ns) for a in e.findall("a:author", ns)][:5],
                    "date": pub.date().isoformat(), "date_source": "arxiv API", "url": e.findtext("a:id", default="", namespaces=ns),
                    "snippet": " ".join((e.findtext("a:summary", default="", namespaces=ns)).split())[:600]})
    return out


def search_crossref(q: str, cutoff: dt.datetime, n: int) -> list[dict]:
    url = (f"https://api.crossref.org/works?query={quote_plus(q)}&rows={n}"
           f"&filter=until-created-date:{cutoff.date().isoformat()}&select=DOI,title,author,created,URL,abstract")
    r = _get(url, UA)
    out = []
    for it in r.json().get("message", {}).get("items", []):
        created = _parse_date((it.get("created") or {}).get("date-time"))
        if not created or created >= cutoff or not it.get("title"):
            continue
        out.append({"source": "crossref", "kind": "paper", "title": it["title"][0],
                    "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in it.get("author", [])][:5],
                    "date": created.date().isoformat(), "date_source": "Crossref record", "url": it.get("URL", ""),
                    "snippet": " ".join((it.get("abstract") or "").split())[:600]})
    return out


def search_semanticscholar(q: str, cutoff: dt.datetime, n: int) -> list[dict]:
    s = get_settings()
    headers = dict(UA)
    if s.semantic_scholar_api_key:
        headers["x-api-key"] = s.semantic_scholar_api_key
    url = (f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote_plus(q)}&limit={n}"
           f"&fields=title,year,publicationDate,url,abstract,authors")
    r = _get(url, headers)
    out = []
    for it in r.json().get("data", []):
        d = _parse_date(it.get("publicationDate")) or (latest_date(str(it["year"])) if it.get("year") else None)
        if not d or d >= cutoff:
            continue
        out.append({"source": "semanticscholar", "kind": "paper", "title": it.get("title", ""),
                    "authors": [a.get("name", "") for a in it.get("authors", [])][:5],
                    "date": d.date().isoformat(), "date_source": "Semantic Scholar record", "url": it.get("url", ""),
                    "snippet": " ".join((it.get("abstract") or "").split())[:600]})
    return out


def search_acr(db: Session, q: str, cutoff: dt.datetime, n: int, exclude_paper_id: int | None = None) -> list[dict]:
    from .models import Paper
    words = [w for w in q.replace(",", " ").split() if len(w) > 3][:6]
    if not words:
        return []
    query = db.query(Paper).filter(Paper.public_at.isnot(None), Paper.priority_at < cutoff)
    if exclude_paper_id:
        query = query.filter(Paper.id != exclude_paper_id)
    conds = [or_(Paper.title.ilike(f"%{w}%"), Paper.abstract.ilike(f"%{w}%")) for w in words]
    query = query.filter(or_(*conds)).order_by(Paper.score_creativity.desc().nullslast()).limit(n)
    return [{"source": "acr", "kind": "paper", "title": p.title, "authors": [p.author.name],
             "date": p.priority_at.date().isoformat(), "date_source": "ACR priority timestamp",
             "url": f"{get_settings().base_url}/papers/{p.acr_id}", "acr_id": p.acr_id, "snippet": p.abstract[:600]}
            for p in query.all()]


def run_dated_api_search(db: Session, queries: list[str], cutoff: dt.datetime, exclude_paper_id: int | None = None) -> tuple[list[dict], list[dict]]:
    s = get_settings()
    sources = [x.strip() for x in s.priorart_sources.split(",") if x.strip()]
    n = s.priorart_max_results_per_source
    items, errors = [], []
    for q in queries[:6]:
        for src in sources:
            try:
                if src == "acr":
                    res = search_acr(db, q, cutoff, n, exclude_paper_id)
                elif src == "arxiv":
                    res = search_arxiv(q, cutoff, n)
                elif src == "crossref":
                    res = search_crossref(q, cutoff, n)
                elif src == "semanticscholar":
                    res = search_semanticscholar(q, cutoff, n)
                else:
                    continue
            except Exception as e:
                errors.append({"source": src, "query": q, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                continue
            for it in res:
                it["query"] = q
                it["found_by"] = [src]
                items.append(it)
    return items, errors


# ----------------------------------------------------------------------------- authoritative date checks
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", re.I)
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.I)


def verify_date(url: str) -> tuple[dt.datetime | None, str]:
    """Return (authoritative date, source) for arXiv and DOI URLs; (None, '') otherwise or on failure."""
    try:
        m = _ARXIV_RE.search(url or "")
        if m:
            r = _get(f"https://export.arxiv.org/api/query?id_list={m.group(1)}", UA)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            e = ET.fromstring(r.text).find("a:entry", ns)
            if e is not None:
                return _parse_date(e.findtext("a:published", default="", namespaces=ns)), "arxiv API"
        m = _DOI_RE.search(url or "")
        if m and "doi.org" in url:
            r = _get(f"https://api.crossref.org/works/{quote_plus(m.group(1).rstrip('.'))}", UA)
            created = (r.json().get("message", {}).get("created") or {}).get("date-time")
            return _parse_date(created), "Crossref record"
    except Exception:
        return None, ""
    return None, ""


# ----------------------------------------------------------------------------- general (model) search
def filter_general_items(raw_items: list[dict], cutoff: dt.datetime, found_by: str, verify: bool = True,
                         max_verify: int = 12) -> tuple[list[dict], list[dict]]:
    """Apply the date rule to model-extracted items. Returns (kept, dropped-with-reason).
    Authoritative date checks (arXiv/DOI) are limited to the first `max_verify` items, which the searcher was
    asked to order by closeness."""
    kept, dropped = [], []
    verified = 0
    for it in raw_items:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        d = latest_date(it.get("date"))
        date_source = "model-reported: " + (it.get("date_evidence") or "unspecified")[:120]
        if verify and it.get("url") and verified < max_verify and (_ARXIV_RE.search(it["url"]) or "doi.org" in it["url"]):
            verified += 1
            vd, vsrc = verify_date(it["url"])
            if vd:
                d, date_source = vd, vsrc
        if d is None:
            dropped.append({"title": it["title"], "url": it.get("url", ""), "reason": "no verifiable date"})
            continue
        if d >= cutoff:
            dropped.append({"title": it["title"], "url": it.get("url", ""), "reason": f"dated {d.date()} on/after cutoff"})
            continue
        kind = it.get("kind") if it.get("kind") in KINDS else "web"
        kept.append({"source": it.get("source_name") or "web", "kind": kind, "title": it["title"].strip(),
                     "authors": [], "date": d.date().isoformat(), "date_source": date_source,
                     "url": it.get("url", ""), "snippet": (it.get("snippet") or "")[:600],
                     "relevance": (it.get("relevance") or "")[:400], "found_by": [found_by]})
    return kept, dropped


def merge_items(*groups: list[dict], limit: int = 60) -> list[dict]:
    seen: dict[str, dict] = {}
    order = []
    for group in groups:
        for it in group:
            key = _norm_url(it.get("url")) or it.get("title", "").lower().strip()[:80]
            if not key:
                continue
            if key in seen:
                for fb in it.get("found_by", []):
                    if fb not in seen[key]["found_by"]:
                        seen[key]["found_by"].append(fb)
                if not seen[key].get("relevance") and it.get("relevance"):
                    seen[key]["relevance"] = it["relevance"]
            else:
                seen[key] = dict(it, found_by=list(it.get("found_by", [])))
                order.append(key)
    items = [seen[k] for k in order][:limit]
    for i, it in enumerate(items, 1):
        it["ref"] = f"P{i}"
    return items


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip().lower().split("#")[0].rstrip("/")
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"arxiv\.org/pdf/", "arxiv.org/abs/", u)
    return re.sub(r"v\d+$", "", u) if "arxiv.org/abs/" in u else u


def format_prior_art(bundle: dict) -> str:
    items = bundle.get("items") or []
    if not items:
        return ("(No dated prior art was found. Anything you cite yourself must carry a date before the cutoff; "
                "undated material does not count.)")
    lines = []
    for it in items:
        who = ", ".join(it.get("found_by", []))
        lines.append(f"[{it['ref']}] {it['date']} · {it.get('kind', 'paper')} · {it.get('source', '')} · {it['title']}\n"
                     f"     {it.get('url', '')}  (date via {it.get('date_source', '?')}; found by {who})\n"
                     f"     {(it.get('relevance') or it.get('snippet') or '')[:400]}")
    return "\n".join(lines)
