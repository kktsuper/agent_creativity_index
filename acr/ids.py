"""Permanent paper IDs: ACR-<year>-<6 digit sequence>. Assigned at receipt, never reused."""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from .models import Sequence
from .models import utcnow

ACR_ID_RE = re.compile(r"\bACR-(\d{4})-(\d{6})\b")


def next_acr_id(db: Session, now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    name = f"paper-{now.year}"
    seq = db.get(Sequence, name, with_for_update=True) if db.bind.dialect.name != "sqlite" else db.get(Sequence, name)
    if seq is None:
        seq = Sequence(name=name, value=0)
        db.add(seq)
        db.flush()
    seq.value += 1
    db.flush()
    return f"ACR-{now.year}-{seq.value:06d}"


def find_acr_ids(text: str) -> list[str]:
    seen, out = set(), []
    for m in ACR_ID_RE.finditer(text or ""):
        s = f"ACR-{m.group(1)}-{m.group(2)}"
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
