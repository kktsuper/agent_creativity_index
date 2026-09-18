"""Small key/value store for admin-editable site policy (rate limit, aggregator, ...)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .config import get_settings
from .models import SiteSetting

DEFAULT_KEYS = {
    "rate_limit_enabled": lambda s: s.rate_limit_enabled,
    "rate_limit_submissions_per_day": lambda s: s.rate_limit_submissions_per_day,
    "index_aggregator": lambda s: s.index_aggregator,
    "rebuttal_hours": lambda s: s.rebuttal_hours,
    "rebuttal_token_cap": lambda s: s.rebuttal_token_cap,
    "auto_review": lambda s: True,       # queue official review automatically after desk screen
    "auto_publish": lambda s: True,      # publish automatically when embargo lifts
    "spend_cap_per_run_usd": lambda s: s.spend_cap_per_run_usd,
    "spend_cap_daily_usd": lambda s: s.spend_cap_daily_usd,
}


def ensure_defaults(db: Session) -> None:
    s = get_settings()
    for k, fn in DEFAULT_KEYS.items():
        if db.get(SiteSetting, k) is None:
            db.add(SiteSetting(key=k, value={"v": fn(s)}))
    db.flush()


def get_setting(db: Session, key: str, default=None):
    row = db.get(SiteSetting, key)
    if row is None:
        if key in DEFAULT_KEYS:
            return DEFAULT_KEYS[key](get_settings())
        return default
    return row.value.get("v", default)


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(SiteSetting, key)
    if row is None:
        db.add(SiteSetting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    db.flush()


def all_settings(db: Session) -> dict:
    return {k: get_setting(db, k) for k in DEFAULT_KEYS}
