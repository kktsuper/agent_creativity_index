"""Model price table and cost estimation for the spend cap. Prices are USD per million tokens (input, output).
Unknown models fall back to a conservative Opus-tier price. Override with ACR_MODEL_PRICES_JSON."""
from __future__ import annotations

import json

from .config import get_settings

DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
    "gpt-5": (1.25, 10.0), "gpt-5-mini": (0.25, 2.0), "o3": (2.0, 8.0), "gpt-4.1": (2.0, 8.0),
    "gemini-2.5-pro": (1.25, 10.0), "grok-4": (3.0, 15.0), "deepseek-reasoner": (0.55, 2.19), "deepseek-chat": (0.27, 1.1),
}
FALLBACK = (5.0, 25.0)
SEARCH_CALL_USD = 0.01     # per web search executed by a native search tool (Anthropic: $10 / 1000 searches)


def prices() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    raw = get_settings().model_prices_json
    if raw:
        try:
            for k, v in json.loads(raw).items():
                table[k] = (float(v[0]), float(v[1]))
        except Exception:
            pass
    return table


def price_for(model: str) -> tuple[float, float]:
    table = prices()
    if model in table:
        return table[model]
    for k, v in table.items():   # prefix match, e.g. "gpt-5-2026-01-01"
        if model.startswith(k):
            return v
    return FALLBACK


def cost_usd(model: str, input_tokens: int, output_tokens: int, searches: int = 0) -> float:
    pin, pout = price_for(model)
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout + searches * SEARCH_CALL_USD, 6)


def projected_call_usd(model: str, prompt_chars: int, max_output_tokens: int, searches: int = 0) -> float:
    """Upper-ish bound for a call about to be made: prompt at 4 chars/token, output at half of max_tokens."""
    return cost_usd(model, prompt_chars // 4 + 500, max_output_tokens // 2, searches)
