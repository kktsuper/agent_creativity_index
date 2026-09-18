from __future__ import annotations

import os

from openai import OpenAI

from . import LLMResult, LLMError, OPENAI_COMPAT_PRESETS, extract_json
from ..config import get_settings

_clients: dict[str, OpenAI] = {}


def _client(provider: str, params: dict) -> OpenAI:
    preset = OPENAI_COMPAT_PRESETS[provider]
    key_env = params.get("key_env") or preset["key_env"]
    base_url = params.get("base_url") or preset.get("base_url")
    if provider == "openai_compat" and not base_url:
        base_url = os.environ.get("OPENAI_COMPAT_BASE_URL")
    key = os.environ.get(key_env) or preset.get("default_key") or ""
    ck = f"{provider}|{base_url}|{key[-6:]}"
    if ck not in _clients:
        _clients[ck] = OpenAI(api_key=key, base_url=base_url, timeout=get_settings().llm_timeout_seconds, max_retries=3)
    return _clients[ck]


def complete_openai_compat(spec, system, messages, json_schema, max_tokens) -> LLMResult:
    provider = spec["provider"]
    params = spec.get("params", {}) or {}
    client = _client(provider, params)
    msgs = [{"role": "system", "content": system}] + messages
    kwargs = dict(model=spec["model"], messages=msgs)
    if params.get("max_tokens_param", "max_completion_tokens") == "max_tokens":
        kwargs["max_tokens"] = max_tokens
    else:
        kwargs["max_completion_tokens"] = max_tokens
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]
    if params.get("reasoning_effort"):
        kwargs["reasoning_effort"] = params["reasoning_effort"]
    if json_schema and params.get("json_mode", "schema") == "schema":
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "response", "schema": json_schema, "strict": False}}
    elif json_schema:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        if json_schema and "response_format" in kwargs:   # some servers reject json_schema; retry loosely
            kwargs.pop("response_format")
            msgs[0]["content"] += "\n\nRespond with a single JSON object matching the requested schema and nothing else."
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e2:
                raise LLMError(f"{provider}: {e2}") from e2
        else:
            raise LLMError(f"{provider}: {e}") from e
    choice = resp.choices[0]
    text = choice.message.content or ""
    usage = resp.usage
    return LLMResult(text=text, json=extract_json(text) if json_schema else None,
                     input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                     output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                     raw={"finish_reason": choice.finish_reason})
