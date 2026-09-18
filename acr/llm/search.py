"""Provider-native web search. Each frontier model searches with its own tool (Anthropic web_search/web_fetch,
OpenAI Responses web_search, Google Search grounding, xAI Live Search) and returns a free-text report with URLs
and dates. Providers without a native search tool raise NoSearchTool and are skipped by the caller.
"""
from __future__ import annotations

import os

from . import LLMResult, LLMError, OPENAI_COMPAT_PRESETS
from ..config import get_settings


class NoSearchTool(LLMError):
    pass


def search_complete(spec: dict, system: str, prompt: str, max_tokens: int = 8000, max_searches: int = 8,
                    cutoff_date: str | None = None) -> LLMResult:
    s = get_settings()
    provider = spec.get("provider", "mock")
    if s.llm_mode == "mock":
        provider = "mock"
    elif provider != "mock":
        from . import provider_available
        ok, why = provider_available(spec)
        if not ok:
            if s.missing_key_policy == "fail":
                raise LLMError(f"No credentials for provider {provider} ({why})")
            r = _mock_search(spec, prompt)
            r.provider, r.model = "mock", spec.get("model", "")
            r.note = f"MOCK: credentials for {provider} ({why}) not configured"
            return r
    if provider == "mock":
        r = _mock_search(spec, prompt)
        r.provider, r.model = "mock", spec.get("model", "")
        return r
    import time
    t0 = time.time()
    if provider == "anthropic":
        r = _anthropic(spec, system, prompt, max_tokens, max_searches)
    elif provider == "openai":
        r = _openai_responses(spec, system, prompt, max_tokens)
    elif provider == "xai":
        r = _xai(spec, system, prompt, max_tokens, cutoff_date)
    elif provider == "google":
        r = _google(spec, system, prompt, max_tokens)
    else:
        raise NoSearchTool(f"provider {provider} has no native search tool")
    r.provider, r.model = provider, spec.get("model", "")
    r.latency_ms = int((time.time() - t0) * 1000)
    return r


# ----------------------------------------------------------------------------- anthropic
def _anthropic(spec, system, prompt, max_tokens, max_searches) -> LLMResult:
    import anthropic
    from .anthropic_p import _c
    params = spec.get("params", {}) or {}
    # "basic" (default): plain web_search + web_fetch, one query per call, predictable budget.
    # "dynamic": the 20260209 variants with server-side filtering; more capable but the model tends to spend the
    # whole search budget in one batched code-execution step, which cost ~270k input tokens in testing.
    # web_fetch is off by default: fetched pages inflate the context that every later search iteration re-reads.
    fetches = int(params.get("search_fetches", 0))
    if params.get("search_variant", "basic") == "dynamic":
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
        if fetches:
            tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches})
    else:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]
        if fetches:
            tools.append({"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": fetches})
    prompt = prompt + f"\n\nYou have at most {max_searches} searches; run them one at a time and vary the phrasing."
    messages = [{"role": "user", "content": prompt}]
    kwargs = dict(model=spec["model"], max_tokens=max_tokens, system=system, tools=tools,
                  output_config={"effort": params.get("search_effort", "medium")})
    if not spec["model"].startswith("claude-fable"):
        kwargs["thinking"] = {"type": "adaptive"}
    texts, citations, in_tok, out_tok = [], [], 0, 0
    for _ in range(6):   # pause_turn continuations
        try:
            with _c().messages.stream(messages=messages, **kwargs) as st:
                msg = st.get_final_message()
        except anthropic.BadRequestError as e:
            if "web_search" in str(e) or "web_fetch" in str(e):   # tool variant unsupported here: plain search only
                kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]
                with _c().messages.stream(messages=messages, **kwargs) as st:
                    msg = st.get_final_message()
            else:
                raise LLMError(f"anthropic search: {e.message}") from e
        in_tok += msg.usage.input_tokens
        out_tok += msg.usage.output_tokens
        for b in msg.content:
            t = getattr(b, "type", "")
            if t == "text":
                texts.append(b.text)
                for c in (getattr(b, "citations", None) or []):
                    u = getattr(c, "url", None)
                    if u:
                        citations.append(u)
            elif t == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for r in b.content:
                    u = getattr(r, "url", None)
                    if u:
                        citations.append(u)
        if msg.stop_reason == "refusal":
            raise LLMError("anthropic refusal during search")
        if msg.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": msg.content}]
    return LLMResult(text="\n".join(texts), json=None, input_tokens=in_tok, output_tokens=out_tok,
                     raw={"citations": sorted(set(citations)), "stop_reason": msg.stop_reason})


# ----------------------------------------------------------------------------- openai (Responses API)
def _openai_responses(spec, system, prompt, max_tokens) -> LLMResult:
    from .openai_p import _client
    client = _client("openai", spec.get("params", {}) or {})
    params = spec.get("params", {}) or {}
    kwargs = dict(model=spec["model"], tools=[{"type": "web_search"}], instructions=system, input=prompt,
                  max_output_tokens=max_tokens)
    if params.get("reasoning_effort"):
        kwargs["reasoning"] = {"effort": params["reasoning_effort"]}
    try:
        resp = client.responses.create(**kwargs)
    except Exception as e:
        if "web_search" in str(e):
            kwargs["tools"] = [{"type": "web_search_preview"}]
            try:
                resp = client.responses.create(**kwargs)
            except Exception as e2:
                raise LLMError(f"openai search: {e2}") from e2
        else:
            raise LLMError(f"openai search: {e}") from e
    urls = []
    for item in getattr(resp, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            for a in getattr(c, "annotations", []) or []:
                if getattr(a, "url", None):
                    urls.append(a.url)
    u = getattr(resp, "usage", None)
    return LLMResult(text=resp.output_text or "", json=None, input_tokens=getattr(u, "input_tokens", 0) or 0,
                     output_tokens=getattr(u, "output_tokens", 0) or 0, raw={"citations": sorted(set(urls))})


# ----------------------------------------------------------------------------- xai (Live Search)
def _xai(spec, system, prompt, max_tokens, cutoff_date) -> LLMResult:
    from .openai_p import _client
    client = _client("xai", spec.get("params", {}) or {})
    sp = {"mode": "on", "return_citations": True,
          "sources": [{"type": "web"}, {"type": "news"}, {"type": "x"}]}
    if cutoff_date:
        sp["to_date"] = cutoff_date   # xAI filters results by date server-side
    try:
        resp = client.chat.completions.create(model=spec["model"], max_tokens=max_tokens,
                                              messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                                              extra_body={"search_parameters": sp})
    except Exception as e:
        raise LLMError(f"xai search: {e}") from e
    text = resp.choices[0].message.content or ""
    cites = getattr(resp, "citations", None) or (resp.model_extra or {}).get("citations", []) if hasattr(resp, "model_extra") else []
    u = resp.usage
    return LLMResult(text=text, json=None, input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                     output_tokens=getattr(u, "completion_tokens", 0) or 0, raw={"citations": list(cites or [])})


# ----------------------------------------------------------------------------- google (Search grounding)
def _google(spec, system, prompt, max_tokens) -> LLMResult:
    from google.genai import types
    from .google_p import _c
    try:
        resp = _c().models.generate_content(
            model=spec["model"], contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens,
                                               tools=[types.Tool(google_search=types.GoogleSearch())]))
    except Exception as e:
        raise LLMError(f"google search: {e}") from e
    urls = []
    try:
        gm = resp.candidates[0].grounding_metadata
        for ch in gm.grounding_chunks or []:
            if ch.web and ch.web.uri:
                urls.append(ch.web.uri)
    except Exception:
        pass
    um = getattr(resp, "usage_metadata", None)
    return LLMResult(text=resp.text or "", json=None, input_tokens=getattr(um, "prompt_token_count", 0) or 0,
                     output_tokens=getattr(um, "candidates_token_count", 0) or 0, raw={"citations": sorted(set(urls))})


# ----------------------------------------------------------------------------- mock
def _mock_search(spec, prompt) -> LLMResult:
    import hashlib, re
    m = re.search(r"^Title:\s*(.+)$", prompt, re.M)
    title = (m.group(1).strip() if m else "the topic")
    h = hashlib.sha256((spec.get("model", "") + title).encode()).hexdigest()[:8]
    text = (f"Search report (mock, {spec.get('model')}).\n"
            f"1. Paper: 'Earlier work on {title[:40]}' (2019-05-14) https://example.org/paper/{h} — closely related method.\n"
            f"2. Patent: US 10,{h[:3]},{h[3:6]} 'System for {title[:30]}' filed 2017-03-02 https://patents.google.com/patent/US10{h[:6]}\n"
            f"3. Product: '{title[:25]} Toolkit' release notes, 2021-11-01 https://example.com/product/{h}\n"
            f"4. News: 'Startup announces {title[:30]}' 2022-08-09 https://news.example.com/{h}\n"
            f"5. Undated blog post https://blog.example.com/{h} — no date visible.\n")
    return LLMResult(text=text, json=None, input_tokens=len(prompt) // 4, output_tokens=len(text) // 4, note="mock",
                     raw={"citations": [f"https://example.org/paper/{h}", f"https://patents.google.com/patent/US10{h[:6]}"]})
