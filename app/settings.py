from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def enable_local_only_defaults() -> None:
    """Disable Hugging Face Hub network checks unless an operator opts out."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HOUSEHOLD_", extra="ignore")

    database_url: str = "sqlite:///./data/household.db"

    # -- Inference (BUILD IT: Ollama; SHIP IT: Bedrock — Ticket #33) --------
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    vision_model: str = "qwen2.5vl:3b"
    vision_request_timeout_seconds: float = Field(default=60.0, gt=0)
    whisper_model: str = "base"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    bedrock_model_id: str | None = None  # Ticket #33; unset in BUILD IT
    aws_region: str = "ap-south-1"

    # -- Remote recipe generation (primary, with local Ollama fallback) -----
    # Keys intentionally default to None. The recipe provider chain skips an
    # unconfigured remote provider and falls back safely rather than sending
    # a request with an empty credential.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    recipe_provider_timeout_seconds: float = Field(default=20.0, gt=0)

    # -- Google Calendar (guest/availability context, B019) -----------------
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://127.0.0.1:8000/api/google-calendar/callback"
    google_token_encryption_key: str | None = None
    # Ticket #38: narrowed from the unverified calendar.events.owned scope.
    google_calendar_scopes: str = (
        "openid email "
        "https://www.googleapis.com/auth/calendar.freebusy "
        "https://www.googleapis.com/auth/calendar.events.readonly"
    )

    # -- Zepto (commerce, existing) ------------------------------------------
    zepto_mcp_url: str = "https://mcp.zepto.co.in/mcp"
    zepto_client_id: str | None = None
    zepto_client_secret: str | None = None
    zepto_oauth_authorization_url: str | None = None
    zepto_oauth_token_url: str | None = None
    zepto_redirect_uri: str = "http://127.0.0.1:8000/api/zepto/callback"
    zepto_token_encryption_key: str | None = None
    zepto_mock_enabled: bool = False

    # Of the *_mock_enabled flags below, only zepto_mock_enabled changes
    # behaviour: it is the one rail with both a mock and a live code path
    # wired up. The others are reporting-only. They still earn their place --
    # startup_warnings() names them so nobody demos a mock as live -- but do
    # not read them expecting to switch a provider. app/core/container.py is
    # where provider selection actually happens, and it selects the mocks
    # unconditionally today.

    # -- Second commerce provider (Ticket #20 — B015/B020 cross-app compare) -
    commerce_second_provider_name: str = "blinkit"
    commerce_mock_enabled: bool = True

    # -- Pine Labs P3P/Grantex (Ticket #18 mock, #40 live — Q4, BLOCKED on RQ4/RQ5) -
    pinelabs_client_id: str | None = None
    pinelabs_client_secret: str | None = None
    pinelabs_p3p_base_url: str | None = None
    pinelabs_mock_enabled: bool = True
    pinelabs_token_encryption_key: str | None = None

    # -- Delhivery Maps (Ticket #19 mock, #39 live — Q4/Q5) ------------------
    delhivery_api_key: str | None = None
    delhivery_maps_base_url: str | None = None
    delhivery_mock_enabled: bool = True

    # -- Gnani voice (Ticket #25 local stub, #41 live — BLOCKED on RQ7) ------
    gnani_api_key: str | None = None
    gnani_base_url: str | None = None
    gnani_mock_enabled: bool = True

    # -- Auth gate (Ticket #7) ------------------------------------------------
    api_key: str | None = None
    bind_host: str = "127.0.0.1"

    # -- CORS (moved out of main.py per Ticket #6) ----------------------------
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    cors_origin_regex: str = r"^http://(localhost|127\.0\.0\.1):\d+$"

    # -- Deterministic thresholds (Ticket #11 — never magic numbers inline) --
    spend_tier_green_ceiling_inr: float = 300.0
    spend_tier_red_floor_inr: float = 1500.0
    spend_tier_unusual_multiplier: float = 2.0
    inventory_recency_window_hours: int = 48
    loop_unclosed_timeout_hours: int = 6
    # Below this delivery-confidence score, consolidate_orders routes to
    # manual purchase rather than trusting a quick-commerce order (B008).
    delivery_confidence_threshold: float = Field(default=0.4, ge=0, le=1)
    # How long the cook has before the meal, used as the delivery deadline
    # when scoring confidence.
    delivery_deadline_minutes: int = Field(default=25, gt=0)
    # Minimum share of a generated recipe's distinct purchasable ingredients
    # that must already be fully stocked before the recipe is accepted.
    recipe_min_stocked_ingredient_ratio: float = Field(default=0.60, ge=0, le=1)
    # How often the unclosed-loop sweep runs.
    unclosed_sweep_interval_seconds: int = Field(default=3600, gt=0)

    @property
    def database_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix) or self.database_url == "sqlite:///:memory:":
            return None
        return Path(self.database_url.removeprefix(prefix))

    def startup_warnings(self) -> list[str]:
        """Ticket #6: validate encryption keys and rail config at startup
        with a WARNING, never a crash — BUILD IT must still boot fully
        offline with no rail credentials at all."""
        warnings: list[str] = []
        if not self.google_token_encryption_key:
            warnings.append("HOUSEHOLD_GOOGLE_TOKEN_ENCRYPTION_KEY is unset — Google Calendar connect will fail.")
        if not self.zepto_token_encryption_key:
            warnings.append("HOUSEHOLD_ZEPTO_TOKEN_ENCRYPTION_KEY is unset — Zepto connect will fail.")
        if not self.pinelabs_token_encryption_key and not self.pinelabs_mock_enabled:
            warnings.append("HOUSEHOLD_PINELABS_TOKEN_ENCRYPTION_KEY is unset and mock is disabled.")
        if not self.api_key:
            warnings.append(
                "HOUSEHOLD_API_KEY is unset — the API will refuse to bind to a non-loopback host (Ticket #7)."
            )
        active_mocks = [
            name
            for name, flag in (
                ("zepto", self.zepto_mock_enabled),
                ("commerce_second_provider", self.commerce_mock_enabled),
                ("pinelabs", self.pinelabs_mock_enabled),
                ("delhivery", self.delhivery_mock_enabled),
                ("gnani", self.gnani_mock_enabled),
            )
            if flag
        ]
        if active_mocks:
            warnings.append(f"Active mocks: {', '.join(active_mocks)} — do not present these as live to a judge.")
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
