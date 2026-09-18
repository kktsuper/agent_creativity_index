"""Per-key submission rate limit. A safety valve, not a policy: any volume is accepted when it is off."""
from __future__ import annotations

import datetime as dt

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import ApiUsage, Author
from .settings_store import get_setting
from .models import utcnow


def check_and_record(db: Session, author: Author, action: str = "submit") -> None:
    if author.rate_limit_exempt or not get_setting(db, "rate_limit_enabled", True):
        db.add(ApiUsage(author_id=author.id, action=action))
        return
    limit = int(get_setting(db, "rate_limit_submissions_per_day", 20))
    since = utcnow() - dt.timedelta(days=1)
    n = db.query(func.count(ApiUsage.id)).filter(ApiUsage.author_id == author.id,
                                                  ApiUsage.action == action, ApiUsage.at >= since).scalar()
    if n >= limit:
        raise HTTPException(429, f"Rate limit: {limit} {action}s per 24h per key. Contact admin to raise it.")
    db.add(ApiUsage(author_id=author.id, action=action))
