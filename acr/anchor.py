"""Anchor a content hash to a public timestamp service.

Default backend: OpenTimestamps. We POST the raw sha256 digest to public calendar servers;
each returns a pending proof (.ots bytes) that commits the digest into a Bitcoin transaction
within a few hours. The proof is stored and downloadable from the paper page; anyone can
verify it with `ots verify` or upgrade it with `ots upgrade`. A 'local' backend exists for
tests and offline development and is clearly labeled as non-public.
"""
from __future__ import annotations

import base64
import hashlib

import httpx

from .config import get_settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def anchor_digest(digest_hex: str) -> list[dict]:
    """Returns one record per calendar attempted: {backend, calendar_url, status, proof_b64, detail}."""
    s = get_settings()
    if s.anchor_backend != "opentimestamps":
        return [{"backend": "local", "calendar_url": "", "status": "anchored",
                 "proof_b64": base64.b64encode(("local:" + digest_hex).encode()).decode(),
                 "detail": "Local (non-public) anchor. Enable OpenTimestamps in production."}]
    out = []
    digest = bytes.fromhex(digest_hex)
    for cal in [c.strip() for c in s.ots_calendars.split(",") if c.strip()]:
        rec = {"backend": "opentimestamps", "calendar_url": cal, "status": "failed", "proof_b64": "", "detail": ""}
        try:
            r = httpx.post(f"{cal}/digest", content=digest,
                           headers={"Accept": "application/vnd.opentimestamps.v1",
                                    "User-Agent": "acr/0.1"}, timeout=15)
            if r.status_code == 200 and r.content:
                rec["status"] = "pending"   # pending until the calendar's Bitcoin commitment confirms
                rec["proof_b64"] = base64.b64encode(_wrap_ots(digest, r.content)).decode()
                rec["detail"] = "Calendar accepted digest; proof upgrades to a Bitcoin attestation within hours."
            else:
                rec["detail"] = f"HTTP {r.status_code}"
        except Exception as e:  # network failure: keep the record, retry later via job
            rec["detail"] = f"{type(e).__name__}: {e}"
        out.append(rec)
    return out


# Minimal .ots file framing so the stored proof is a valid OpenTimestamps file:
# header magic + version + sha256 op + digest + calendar attestation timestamp path.
_OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"


def _wrap_ots(digest: bytes, calendar_proof: bytes) -> bytes:
    # Version 1, file hash op = sha256 (0x08), followed by the digest and the calendar's timestamp
    return _OTS_MAGIC + b"\x01" + b"\x08" + digest + calendar_proof
