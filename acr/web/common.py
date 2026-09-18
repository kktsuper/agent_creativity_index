from __future__ import annotations

import datetime as dt
import json
import os

import bleach
import markdown as md_lib
from fastapi.templating import Jinja2Templates

from ..config import get_settings

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + ["p", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead",
                                                      "tbody", "tr", "th", "td", "img", "hr", "br", "sup", "sub", "span", "div"]
ALLOWED_ATTRS = {"a": ["href", "title"], "img": ["src", "alt"], "span": ["class"], "div": ["class"], "code": ["class"],
                 "td": ["align"], "th": ["align"]}


def render_md(text: str) -> str:
    html = md_lib.markdown(text or "", extensions=["tables", "fenced_code", "toc", "sane_lists"])
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=["http", "https", "mailto"])


def fmt_dt(d: dt.datetime | None, with_time: bool = True) -> str:
    if not d:
        return "—"
    return d.strftime("%Y-%m-%d %H:%M UTC") if with_time else d.strftime("%Y-%m-%d")


def fmt_score(x) -> str:
    return "—" if x is None else f"{x:.1f}"


def tojson_pretty(x) -> str:
    return json.dumps(x, indent=2, ensure_ascii=False, default=str)


templates.env.filters["md"] = render_md
templates.env.filters["dt"] = fmt_dt
templates.env.filters["date"] = lambda d: fmt_dt(d, False)
templates.env.filters["score"] = fmt_score
templates.env.filters["pretty"] = tojson_pretty
templates.env.globals["settings"] = get_settings
templates.env.globals["now"] = dt.datetime.utcnow
