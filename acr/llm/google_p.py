from __future__ import annotations

import os

from . import LLMResult, LLMError, extract_json

_client = None


def _c():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return _client


def complete_google(spec, system, messages, json_schema, max_tokens) -> LLMResult:
    from google.genai import types
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
    cfg = dict(system_instruction=system, max_output_tokens=max_tokens)
    if json_schema:
        cfg["response_mime_type"] = "application/json"
        cfg["response_json_schema"] = json_schema
    try:
        resp = _c().models.generate_content(model=spec["model"], contents=contents,
                                            config=types.GenerateContentConfig(**cfg))
    except Exception as e:
        if json_schema:   # older SDKs lack response_json_schema: retry with instruction only
            cfg.pop("response_json_schema", None)
            cfg["system_instruction"] = system + "\n\nRespond with a single JSON object matching the requested schema."
            try:
                resp = _c().models.generate_content(model=spec["model"], contents=contents,
                                                    config=types.GenerateContentConfig(**cfg))
            except Exception as e2:
                raise LLMError(f"google: {e2}") from e2
        else:
            raise LLMError(f"google: {e}") from e
    text = resp.text or ""
    um = getattr(resp, "usage_metadata", None)
    return LLMResult(text=text, json=extract_json(text) if json_schema else None,
                     input_tokens=getattr(um, "prompt_token_count", 0) or 0,
                     output_tokens=getattr(um, "candidates_token_count", 0) or 0)
