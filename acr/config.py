"""Runtime configuration. Everything comes from environment variables (or .env)."""
from __future__ import annotations

import os
from functools import lru_cache
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Provider keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) are read from os.environ by the SDKs, so a .env file
# must be exported into the environment, not only parsed for ACR_* settings.
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACR_", env_file=".env", extra="ignore")

    # --- core ---
    env: str = "dev"                       # dev | prod
    base_url: str = "http://localhost:8080"
    secret_key: str = "change-me-in-prod"   # signs admin session cookies
    admin_password: str = "admin"           # admin dashboard login
    registration_token: str = ""            # if set, POST /authors/register must include it
    database_url: str = "sqlite:///./data/acr.sqlite3"
    data_dir: str = "./data"

    # --- storage: local | gcs ---
    storage_backend: str = "local"
    gcs_bucket: str = ""

    # --- timestamp anchoring: opentimestamps | local ---
    anchor_backend: str = "opentimestamps"
    ots_calendars: str = "https://a.pool.opentimestamps.org,https://b.pool.opentimestamps.org,https://a.pool.eternitywall.com"

    # --- LLM ---
    llm_mode: str = "live"                  # live | mock  (mock = deterministic fake models, no keys needed)
    missing_key_policy: str = "fail"        # fail | mock  (mock is for local development only; tests use llm_mode=mock)
    llm_timeout_seconds: float = 600.0
    anthropic_fallbacks: bool = True        # server-side refusal fallbacks on Claude models

    # --- spend caps (USD). Per-run: the run stops (fails) before a call that would exceed it.
    # Daily: new review work pauses for an hour once the day's model spend reaches it. Both editable in admin.
    spend_cap_per_run_usd: float = 5.0
    spend_cap_daily_usd: float = 50.0
    model_prices_json: str = ""             # optional override: {"model": [in_usd_per_mtok, out_usd_per_mtok], ...}

    # --- review flow ---
    rebuttal_hours: int = 24
    rebuttal_token_cap: int = 1500
    worker_poll_seconds: float = 2.0
    sandbox_rebuttal_hours: float = 0.0     # test reviews never wait for an author

    # --- volume ---
    rate_limit_enabled: bool = True
    rate_limit_submissions_per_day: int = 20
    max_paper_bytes: int = 5 * 1024 * 1024
    max_attachment_bytes: int = 50 * 1024 * 1024
    max_attachments: int = 10

    # --- prior art ---
    priorart_sources: str = "acr,arxiv,crossref,semanticscholar"
    priorart_max_results_per_source: int = 8
    semantic_scholar_api_key: str = ""

    # --- index ---
    index_aggregator: str = "decay_half"     # see acr/scoring.py AGGREGATORS

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
