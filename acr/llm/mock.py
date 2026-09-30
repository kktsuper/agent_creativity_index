"""Deterministic schema-driven fake model. Same inputs -> same outputs. Scores depend on a hash of the
paper text and the model name so different 'labs' disagree a little, like real committees do."""
from __future__ import annotations

import hashlib
import json

from . import LLMResult


def _h(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest(), 16)


def _fill(schema: dict, seed: str, path: str = "") -> object:
    t = schema.get("type")
    if "enum" in schema:
        opts = schema["enum"]
        # bias decisions toward accept for demo pleasantness, but keep some rejects
        if "accept" in opts:
            return "accept" if _h(seed, path) % 10 < 7 else "reject"
        if "patent" in opts and "[0]" in path:
            return "patent"
        return opts[_h(seed, path) % len(opts)]
    if t == "object":
        return {k: _fill(v, seed, f"{path}.{k}") for k, v in schema.get("properties", {}).items()}
    if t == "array":
        if "quer" in path and schema.get("items", {}).get("type") == "string":
            return _title_queries(seed)
        if path.endswith("tags"):
            return _mock_tags(seed)
        n = 2 if schema.get("items", {}).get("type") in ("string", "object") else 1
        if "arxiv_id" in schema.get("items", {}).get("properties", {}) and _IDS_CACHE.get(seed):
            n = len(_IDS_CACHE[seed])
        return [_fill(schema.get("items", {"type": "string"}), seed, f"{path}[{i}]") for i in range(n)]
    if t == "number" or t == "integer":
        lo, hi = schema.get("minimum", 0), schema.get("maximum", 10)
        v = lo + (_h(seed, path) % 1000) / 1000 * (hi - lo)
        if t == "integer":
            return int(round(v))
        return round(lo + 0.3 * (hi - lo) + 0.6 * (v - lo), 1)   # squeeze toward the middle-upper band
    if t == "boolean":
        return _h(seed, path) % 3 == 0
    if t == "string":
        leaf = path.rsplit(".", 1)[-1].strip("[]0123456789") or "text"
        if leaf == "date":
            return "" if "[1]" in path else f"20{10 + _h(seed, path) % 14:02d}-{1 + _h(seed, path, 'm') % 12:02d}-{1 + _h(seed, path, 'd') % 28:02d}"
        if leaf == "arxiv_id":
            ids = _IDS_CACHE.get(seed, [])
            import re as _re
            k = _re.search(r"\[(\d+)\]\.arxiv_id$", path)
            return ids[int(k.group(1))] if k and int(k.group(1)) < len(ids) else "0000.00000"
        if leaf == "url":
            return f"https://example.org/{hashlib.sha256((seed + path).encode()).hexdigest()[:10]}"
        return f"[mock] {leaf.replace('_', ' ')} — generated deterministically from the submission by the mock model"
    return None


_TITLE_CACHE: dict[str, list[str]] = {}
_IDS_CACHE: dict[str, list[str]] = {}


# Mixed spellings on purpose, so the mock pipeline exercises tag normalization ("LLMs" / "llm").
_TAG_POOL = ["LLMs", "llm", "Reinforcement Learning", "world models", "combinatorics", "graph theory",
             "theorem proving", "optimization", "Video Generation", "robotics"]


def _mock_tags(seed: str) -> list[str]:
    start = _h(seed, "tags") % len(_TAG_POOL)
    return [_TAG_POOL[(start + 3 * i) % len(_TAG_POOL)] for i in range(3)]


def _title_queries(seed: str) -> list[str]:
    """Search queries built from the submission title (stashed by complete_mock), so prior-art search is meaningful."""
    words = [w for w in _TITLE_CACHE.get(seed, "research paper").replace(":", " ").split() if len(w) > 3][:8]
    if not words:
        return ["research paper"]
    return [" ".join(words[:4]), " ".join(words[2:6]) or " ".join(words), " ".join(words[-3:])]


def complete_mock(spec, system, messages, json_schema, max_tokens) -> LLMResult:
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    seed = spec.get("model", "mock") + "::" + hashlib.sha256(user_text.encode()).hexdigest()
    import re
    m = re.search(r"^Title:\s*(.+)$", user_text, re.M)
    if m:
        _TITLE_CACHE[seed] = m.group(1).strip()
    _IDS_CACHE[seed] = re.findall(r"^\[(\d{4}\.\d{4,5})\]", user_text, re.M)
    if json_schema:
        obj = _fill(json_schema, seed)
        text = json.dumps(obj, indent=2)
        return LLMResult(text=text, json=obj, input_tokens=len(user_text) // 4, output_tokens=len(text) // 4,
                         note="mock")
    text = f"[mock response from {spec.get('model')}] " + user_text[:200]
    return LLMResult(text=text, json=None, input_tokens=len(user_text) // 4, output_tokens=50, note="mock")
