from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HOUSEHOLD_", extra="ignore")

    database_url: str = "sqlite:///./data/household.db"

    # -- Inference (BUILD IT: Ollama; SHIP IT: Bedrock — Ticket #33) --------
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    vision_model: str = "qwen2.5vl:3b"
    vision_request_timeout_seconds: float = Field(default=60.0, gt=0)
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

    # -- Gnani voice (Ticket #25 local stub, #41 live) -----------------------
    # RQ7 (kitchen-noise robustness) blocks the *STT* leg only; it is a
    # question about a microphone and there is no microphone in text-to-speech.
    # See app/providers/voice_gnani.py for where that gate now sits.
    gnani_api_key: str | None = None
    gnani_base_url: str = "https://api.vachana.ai/api/v1"
    # Unlike the other *_mock_enabled flags, this one now branches: it is read
    # in app/core/container.py to choose between MockVoiceProvider and the
    # live Gnani provider.
    gnani_mock_enabled: bool = True
    gnani_tts_model: str = "timbre-v2.5"
    # Gnani's documented speed range is 0.85–1.15; anything outside it is a
    # request the API will reject, so it is rejected here first. Note this is
    # a TOP-LEVEL request field, not part of audio_config.
    gnani_tts_speed: float = Field(default=1.0, ge=0.85, le=1.15)

    # -- audio_config (a required object in the TTS request body) -------------
    # MP3 because the bytes cross the response body on every cache miss with
    # no CDN in front, and MP3 is roughly an order of magnitude smaller than
    # WAV for speech at equivalent intelligibility.
    gnani_audio_container: str = "mp3"
    # Supported: 8000, 16000, 22050, 24000, 44100, 48000. 24k is plenty for
    # speech; 48k triples the payload for no audible gain on a phone speaker.
    gnani_sample_rate: int = 24000
    gnani_num_channels: int = 1
    gnani_sample_width: int = 2
    # Only sent when the container is mp3. Supported: 32k/64k/96k/128k/192k.
    gnani_mp3_bitrate: str = "128k"
    # Only sent when the container is NOT mp3 — the docs state encoding is not
    # required for mp3.
    gnani_audio_encoding: str = "linear_pcm"

    # The language used when the cook's profile language has no voice of its
    # own. Hinglish, because a code-mixed briefing is what an Indian home cook
    # actually follows, and because leaving eight of the eleven mapped
    # languages with no audio at all was the alternative.
    gnani_default_language: str = "hi-en"
    # Free-text CookProfile.language -> app.enums.Language code.
    # "hindi" deliberately targets the code-mixed hi-en voice: real Indian
    # kitchen speech mixes English nouns ("pressure cooker", "microwave"),
    # and pure hi-IN renders those as awkward transliterations.
    gnani_language_map: dict[str, str] = Field(
        default_factory=lambda: {
            "hindi": "hi-en",
            "hinglish": "hi-en",
            "english": "en-IN",
            "kannada": "kn-IN",
            "tamil": "ta-IN",
            "telugu": "te-IN",
            "malayalam": "ml-IN",
            "marathi": "mr-IN",
            "punjabi": "pa-IN",
            "bengali": "bn-IN",
            "gujarati": "gu-IN",
        }
    )
    # Language code -> Gnani voice name, so voices can be retuned without a
    # code change. `voice` is a required field in the TTS request, so a
    # language absent from this map cannot be synthesized directly — it falls
    # back to gnani_default_language instead.
    #
    # Only the four voices Gnani's REST documentation actually names appear
    # here. The catalog has 42 across ten languages, but inventing plausible
    # names for Tamil or Bengali would produce a 400 "unsupported voice" at
    # the worst possible moment. Add them from the real Voice Catalog once
    # they have been read off it; until then those languages get Hinglish,
    # which is a working briefing rather than a guessed one.
    gnani_voice_map: dict[str, str] = Field(
        default_factory=lambda: {
            "hi-en": "Poorvi",
            "hi-IN": "Nalini",
            "en-IN": "Kaveri",
        }
    )
    # MEASURED, not assumed — but measured loosely, so read the caveat.
    # scripts/probe_gnani_tts.py against the live API observed:
    #   500 / 1000 / 1500 / 2000 chars -> 200 OK
    #   2500 chars -> HTTP 500 "We are facing technical difficulties" (one try)
    #   3000 chars -> HTTP 500 on one run; a later retry was 429 rate-limited,
    #                 which says nothing about length
    # So: 2000 is known good, 2500 failed once, and the boundary between them
    # was not bisected. Note the failure above the ceiling is an unhelpful
    # server error, not a clean 400 naming a limit — which is a reason to stay
    # well clear of it rather than to creep up on it.
    #
    # 1500 rather than 1999 on purpose: the boundary is approximate, the
    # failure above it is ugly, and a normal recipe briefing lands well under
    # it, so the chunker stays a rare path rather than a routine one.
    gnani_tts_max_chars: int = Field(default=1500, gt=0)
    # Per-household, per-day synthesis ceiling, enforced in
    # ToolRegistry._gate_voice. A credit-consuming call is spend, and spend
    # goes through the registry gate like every other kind.
    gnani_daily_synthesis_limit: int = Field(default=50, gt=0)

    # -- Recipe audio cache (ephemeral; never persisted) ----------------------
    recipe_audio_ttl_seconds: int = Field(default=1800, gt=0)
    recipe_audio_cache_max_entries: int = Field(default=32, gt=0)

    def gnani_audio_config(self) -> dict[str, object]:
        """The `audio_config` object sent with every TTS request.

        Built here rather than in the provider so the request's shape has one
        definition. `bitrate` applies only to mp3 and `encoding` is documented
        as not required for mp3, so each is included only where it means
        something — sending both unconditionally is how a 400 arrives with a
        message about a field you did not think you were setting.
        """
        config: dict[str, object] = {
            "container": self.gnani_audio_container,
            "sample_rate": self.gnani_sample_rate,
            "num_channels": self.gnani_num_channels,
            "sample_width": self.gnani_sample_width,
        }
        if self.gnani_audio_container == "mp3":
            config["bitrate"] = self.gnani_mp3_bitrate
        else:
            config["encoding"] = self.gnani_audio_encoding
        return config

    # -- Auth gate (Ticket #7) ------------------------------------------------
    api_key: str | None = None
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, le=65535)
    # Ticket #7, extended for real deployments. `run()`'s bind_host check only
    # fires when the process is started as `python -m app.main`; every hosting
    # platform starts it as `uvicorn app.main:app`, which imports the module
    # and never calls run(), so the guard silently did not protect a deployed
    # instance at all. The application cannot observe which socket uvicorn
    # bound when it is started externally, so the posture is *declared* rather
    # than detected — see is_public_deployment().
    public_deployment: bool = False

    # -- Demo data (deployment only) -----------------------------------------
    # Off by default so neither the test suite nor local development ever gets
    # a background job rewriting its database.
    demo_seed_on_startup: bool = False
    demo_reseed_interval_seconds: int = Field(default=21600, gt=0)  # 6 hours

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
    # How many audit rows the event feed returns, and the ceiling a caller
    # may ask for. The cap exists because the feed is polled after every
    # action on a page that stays open.
    audit_feed_default_limit: int = Field(default=50, gt=0)
    audit_feed_max_limit: int = Field(default=200, gt=0)

    def is_public_deployment(self) -> bool:
        """True when this process is reachable from outside localhost.

        Either declared explicitly via HOUSEHOLD_PUBLIC_DEPLOYMENT, or
        inferred from a platform marker the operator did not have to remember
        to set: Hugging Face Spaces sets SPACE_ID in every container, so if we
        are running there we are public whatever the .env says.

        Deliberately not inferred from `bind_host`: under `uvicorn app.main:app
        --host 0.0.0.0` that setting is never read, and under gunicorn or a
        programmatic uvicorn.Server there is no supported way to ask what was
        bound. A guard that silently fails to fire is worse than no guard, so
        the signal is declared rather than guessed at.
        """
        return self.public_deployment or bool(os.environ.get("SPACE_ID"))

    def public_deployment_error(self) -> str | None:
        """The one refusal this class makes. Everything else in
        startup_warnings() is a warning, because offline boot must always
        succeed — but serving a public URL with no credential is not a
        degraded mode, it is an open database."""
        if self.is_public_deployment() and not self.api_key:
            return (
                "Refusing to serve a public deployment with no HOUSEHOLD_API_KEY configured. "
                "Set HOUSEHOLD_API_KEY, or unset HOUSEHOLD_PUBLIC_DEPLOYMENT to run loopback-only."
            )
        return None

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
        if self.gnani_api_key and self.gnani_mock_enabled:
            warnings.append(
                "HOUSEHOLD_GNANI_API_KEY is set but HOUSEHOLD_GNANI_MOCK_ENABLED is true — "
                "voice stays mocked. Set the mock flag to false to use the live rail."
            )
        if not self.api_key:
            warnings.append(
                "HOUSEHOLD_API_KEY is unset — the API will refuse to bind to a non-loopback host, "
                "and will refuse to start at all if HOUSEHOLD_PUBLIC_DEPLOYMENT is set (Ticket #7)."
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
