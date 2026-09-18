"""Uniform model-calling layer.

complete(spec, system, messages, json_schema) -> LLMResult
spec = {"lab": "anthropic", "provider": "anthropic", "model": "claude-opus-5", "params": {...}}

Providers: anthropic | google | openai-compatible presets (openai, xai, deepseek, mistral, together,
openrouter, ollama, openai_compat) | mock.  In mock mode every provider is replaced by a deterministic
schema-driven fake so the full pipeline can run without keys.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from ..config import get_settings


@dataclass
class LLMResult:
    text: str
    json: dict | None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    provider: str = ""
    model: str = ""
    note: str = ""
    raw: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


OPENAI_COMPAT_PRESETS = {
    "openai":      {"key_env": "OPENAI_API_KEY",     "base_url": None},
    "xai":         {"key_env": "XAI_API_KEY",        "base_url": "https://api.x.ai/v1"},
    "deepseek":    {"key_env": "DEEPSEEK_API_KEY",   "base_url": "https://api.deepseek.com"},
    "mistral":     {"key_env": "MISTRAL_API_KEY",    "base_url": "https://api.mistral.ai/v1"},
    "together":    {"key_env": "TOGETHER_API_KEY",   "base_url": "https://api.together.xyz/v1"},
    "openrouter":  {"key_env": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"},
    "ollama":      {"key_env": "OLLAMA_API_KEY",     "base_url": "http://localhost:11434/v1", "default_key": "ollama"},
    "openai_compat": {"key_env": "OPENAI_COMPAT_API_KEY", "base_url": None},
}


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            return None
    return None


def provider_available(spec: dict) -> tuple[bool, str]:
    """(available, reason). Used by the admin dashboard and by the committee assigner."""
    p = spec.get("provider", "")
    params = spec.get("params", {}) or {}
    if p == "mock":
        return True, "mock"
    if p == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")), "ANTHROPIC_API_KEY"
    if p == "google":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")), "GEMINI_API_KEY"
    if p in OPENAI_COMPAT_PRESETS:
        preset = OPENAI_COMPAT_PRESETS[p]
        env = params.get("key_env") or preset["key_env"]
        if preset.get("default_key"):
            return True, env
        return bool(os.environ.get(env)), env
    return False, f"unknown provider {p!r}"


def complete(spec: dict, system: str, messages: list[dict], json_schema: dict | None = None,
             max_tokens: int = 8000) -> LLMResult:
    s = get_settings()
    provider = spec.get("provider", "mock")
    if s.llm_mode == "mock":
        provider = "mock"
    elif provider != "mock":
        ok, why = provider_available(spec)
        if not ok:
            if s.missing_key_policy == "fail":
                raise LLMError(f"No credentials for provider {provider} ({why})")
            from .mock import complete_mock
            r = complete_mock(spec, system, messages, json_schema, max_tokens)
            r.provider, r.model = "mock", spec.get("model", "")
            r.note = f"MOCK: credentials for {provider} ({why}) not configured; missing_key_policy=mock"
            return r
    t0 = time.time()
    if provider == "mock":
        from .mock import complete_mock
        r = complete_mock(spec, system, messages, json_schema, max_tokens)
    elif provider == "anthropic":
        from .anthropic_p import complete_anthropic
        r = complete_anthropic(spec, system, messages, json_schema, max_tokens)
    elif provider == "google":
        from .google_p import complete_google
        r = complete_google(spec, system, messages, json_schema, max_tokens)
    elif provider in OPENAI_COMPAT_PRESETS:
        from .openai_p import complete_openai_compat
        r = complete_openai_compat(spec, system, messages, json_schema, max_tokens)
    else:
        raise LLMError(f"Unknown provider {provider!r}")
    r.latency_ms = int((time.time() - t0) * 1000)
    r.provider, r.model = provider, spec.get("model", "")
    if json_schema and r.json is None:
        r.json = extract_json(r.text)
    return r
