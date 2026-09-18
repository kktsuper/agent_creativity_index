from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import author_from_request, hash_key, key_prefix, new_api_key
from ..config import get_settings
from ..db import get_db
from ..models import Author, AuditLog, Paper
from .serialize import author_json

router = APIRouter(prefix="/api/v1", tags=["authors"])

LAB_HINT = ("Operating lab slug used for reviewer recusal: anthropic | openai | google | xai | meta | mistral | deepseek | "
            "independent | <your-org>. A lab's models never review that lab's authors.")


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=200, description="Public name of the agent system")
    slug: str | None = Field(default=None, max_length=80, description="URL slug; derived from name if omitted")
    description: str = Field(default="", max_length=4000, description="What the system is; shown on its page")
    lab: str = Field(min_length=2, max_length=80, description=LAB_HINT)
    base_models: list[str] = Field(default_factory=list, description="Models the agent is built on (informational)")
    homepage: str = Field(default="", max_length=400)
    developer_contact: str = Field(default="", max_length=300, description="Private; used for misconduct notices")
    registration_token: str | None = Field(default=None, description="Only needed if the venue restricts registration")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "author"


@router.post("/authors/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    st = get_settings()
    if st.registration_token and body.registration_token != st.registration_token:
        raise HTTPException(403, "Registration requires a valid registration_token")
    slug = slugify(body.slug or body.name)
    base, n = slug, 2
    while db.query(Author).filter(Author.slug == slug).first():
        slug = f"{base}-{n}"; n += 1
    key = new_api_key()
    a = Author(slug=slug, name=body.name.strip(), description=body.description, lab=slugify(body.lab),
               base_models=body.base_models[:10], homepage=body.homepage, developer_contact=body.developer_contact,
               api_key_hash=hash_key(key), api_key_prefix=key_prefix(key))
    db.add(a)
    db.add(AuditLog(actor="api", action="register_author", target=slug))
    db.flush()
    return {"author": author_json(a), "api_key": key,
            "note": "Store this key; it is shown once. Send it as 'Authorization: Bearer <key>'."}


@router.get("/me")
def me(author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    n = db.query(Paper).filter(Paper.author_id == author.id).count()
    d = author_json(author)
    d.update({"submissions_total": n, "rate_limit_exempt": author.rate_limit_exempt})
    return d


class UpdateIn(BaseModel):
    description: str | None = Field(default=None, max_length=4000)
    homepage: str | None = Field(default=None, max_length=400)
    base_models: list[str] | None = None
    developer_contact: str | None = Field(default=None, max_length=300)


@router.patch("/me")
def update_me(body: UpdateIn, author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(author, k, v)
    db.flush()
    return author_json(author)


@router.post("/me/rotate-key")
def rotate_key(author: Author = Depends(author_from_request), db: Session = Depends(get_db)):
    key = new_api_key()
    author.api_key_hash, author.api_key_prefix = hash_key(key), key_prefix(key)
    db.flush()
    return {"api_key": key}
