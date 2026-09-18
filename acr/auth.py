"""API keys for authors and a signed cookie session for the admin."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Author


def new_api_key() -> str:
    return "acr_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str:
    return key[:10]


def author_from_request(request: Request, db: Session = Depends(get_db)) -> Author:
    auth = request.headers.get("authorization", "")
    key = ""
    if auth.lower().startswith("bearer "):
        key = auth[7:].strip()
    elif request.headers.get("x-api-key"):
        key = request.headers["x-api-key"].strip()
    if not key:
        raise HTTPException(401, "Missing API key (Authorization: Bearer <key>)")
    author = db.query(Author).filter(Author.api_key_hash == hash_key(key)).first()
    if author is None:
        raise HTTPException(401, "Invalid API key")
    if author.suspended:
        raise HTTPException(403, f"Author suspended: {author.suspended_reason}")
    return author


# ----------------------------------------------------------------------------- admin session

def _sign(payload: str) -> str:
    sig = hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_admin_cookie(ttl_seconds: int = 12 * 3600) -> str:
    payload = f"admin:{int(time.time()) + ttl_seconds}"
    return payload + ":" + _sign(payload)


def verify_admin_cookie(value: str | None) -> bool:
    if not value:
        return False
    try:
        who, exp, sig = value.split(":")
    except ValueError:
        return False
    payload = f"{who}:{exp}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    return who == "admin" and int(exp) > time.time()


def require_admin(request: Request) -> None:
    if not verify_admin_cookie(request.cookies.get("acr_admin")):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


def check_admin_password(pw: str) -> bool:
    return hmac.compare_digest(pw, get_settings().admin_password)
