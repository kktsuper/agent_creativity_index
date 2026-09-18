from __future__ import annotations

import anthropic

from . import LLMResult, LLMError, extract_json
from ..config import get_settings

_client = None

_UNSUPPORTED = ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength")


def sanitize_schema(schema):
    """Anthropic structured outputs reject numeric/array range keywords; move them into descriptions so the
    model still sees the constraint, and the caller's derive()/clamp keeps the hard guarantee."""
    if isinstance(schema, list):
        return [sanitize_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    notes = []
    for k, v in schema.items():
        if k in _UNSUPPORTED:
            notes.append(f"{k} {v}")
        elif k == "enum":
            out[k] = list(v)
        else:
            out[k] = sanitize_schema(v)
    if notes:
        out["description"] = (out.get("description", "") + " (" + ", ".join(notes) + ")").strip()
    return out


def _c():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=get_settings().llm_timeout_seconds, max_retries=3)
    return _client


def complete_anthropic(spec, system, messages, json_schema, max_tokens) -> LLMResult:
    params = spec.get("params", {}) or {}
    model = spec["model"]
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
    # Adaptive thinking on 4.6+ models; effort controls depth.
    if params.get("thinking", True) and not model.startswith("claude-fable"):
        kwargs["thinking"] = {"type": "adaptive"}
    output_config = {"effort": params.get("effort", "high")}
    if json_schema:
        output_config["format"] = {"type": "json_schema", "schema": sanitize_schema(json_schema)}
    kwargs["output_config"] = output_config

    use_fallbacks = get_settings().anthropic_fallbacks and params.get("fallbacks", True)
    try:
        if use_fallbacks:
            with _c().beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                           fallbacks="default", **kwargs) as st:
                msg = st.get_final_message()
        else:
            with _c().messages.stream(**kwargs) as st:
                msg = st.get_final_message()
    except anthropic.BadRequestError as e:
        if use_fallbacks:  # fallback parameter unsupported on this platform/model: retry plain
            with _c().messages.stream(**kwargs) as st:
                msg = st.get_final_message()
        else:
            raise LLMError(f"anthropic bad request: {e.message}") from e

    if msg.stop_reason == "refusal":
        raise LLMError(f"anthropic refusal: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens" and json_schema:
        raise LLMError(f"anthropic output truncated at max_tokens={max_tokens} (thinking tokens count); raise the harness limit")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return LLMResult(text=text, json=extract_json(text) if json_schema else None,
                     input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens,
                     raw={"stop_reason": msg.stop_reason, "served_model": msg.model})
