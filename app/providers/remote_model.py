"""Shared base for direct HTTP recipe-model providers.

Holds everything Groq and Gemini do identically: configuration, rate-limit
cooldowns, and the whole request/response round trip -- transport errors,
429 handling, HTTP error codes, JSON decoding and schema validation. A
concrete provider supplies only what genuinely differs between the two APIs:
the endpoint, the auth header, the request body, and how to find the
generated text in the response.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic
from typing import Any

import httpx

from app.providers.json_schema import validate_against_schema
from app.providers.model_failures import RecipeProviderOperationalError


@dataclass(frozen=True)
class RemoteModelHealth:
    status: str
    model: str
    detail: str | None = None


class RemoteRecipeModelProvider:
    """Tracks configuration and temporary rate-limit cooldowns.

    This is deliberately in-memory. Cooldown state is operational, not
    household memory, and a process restart should simply permit a fresh
    health attempt.
    """

    provider_name = "remote"

    def __init__(self, api_key: str | None, model: str, timeout_seconds: float) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._cooldown_until = 0.0
        self._lock = Lock()

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        with self._lock:
            return monotonic() >= self._cooldown_until

    def mark_operational_failure(self, error: RecipeProviderOperationalError) -> None:
        """Only quota responses receive a cross-request cooldown.

        Other failures are retried by the next request because they may be a
        short network blip; the current request has already failed over.
        """
        if error.retry_after_seconds is None:
            return
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, monotonic() + error.retry_after_seconds)

    def health(self) -> RemoteModelHealth:
        if not self.api_key:
            return RemoteModelHealth("unconfigured", self.model, "API key is not configured")
        if not self.is_available():
            return RemoteModelHealth("cooldown", self.model, "Provider is temporarily rate limited")
        return RemoteModelHealth("available", self.model)

    def _require_key(self) -> str:
        if not self.api_key:
            raise RecipeProviderOperationalError(f"{self.provider_name} is not configured")
        return self.api_key

    # -- the shared round trip ---------------------------------------------

    def generate_structured(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        """Ask the remote model for one JSON object matching `schema`.

        Every failure mode surfaces as RecipeProviderOperationalError, which
        is what tells the provider chain it may fail over. A recipe that
        parses but is infeasible is emphatically NOT an operational error --
        that judgement belongs to the deterministic planner, not here.
        """
        key = self._require_key()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self._endpoint,
                    headers=self._headers(key),
                    json=self._build_body(prompt, schema),
                )
        except httpx.TimeoutException as exc:
            raise RecipeProviderOperationalError(f"{self.provider_name.title()} timed out") from exc
        except httpx.RequestError as exc:
            raise RecipeProviderOperationalError(f"{self.provider_name.title()} is unreachable") from exc

        if response.status_code == 429:
            raise RecipeProviderOperationalError(
                f"{self.provider_name.title()} rate limit reached",
                retry_after_seconds=retry_after_from_headers(response.headers),
            )
        if response.status_code >= 400:
            raise RecipeProviderOperationalError(
                f"{self.provider_name.title()} request failed with HTTP {response.status_code}"
            )

        try:
            decoded = json.loads(self._extract_text(response.json()))
            if not isinstance(decoded, dict):
                raise ValueError(f"{self.provider_name.title()} response was not a JSON object")
            if schema:
                validate_against_schema(decoded, schema)
            return decoded
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RecipeProviderOperationalError(
                f"{self.provider_name.title()} returned invalid structured output"
            ) from exc

    # -- per-provider hooks -------------------------------------------------

    def _headers(self, api_key: str) -> dict[str, str]:
        raise NotImplementedError

    def _build_body(self, prompt: str, schema: dict | None) -> dict[str, Any]:
        raise NotImplementedError

    def _extract_text(self, payload: dict[str, Any]) -> str:
        """Pull the generated JSON text out of this API's response envelope."""
        raise NotImplementedError


def retry_after_from_headers(headers) -> float:
    """Return a safe provider cooldown, defaulting rate limits to one minute."""
    raw = headers.get("Retry-After") if headers is not None else None
    if raw:
        try:
            return max(float(raw), 0.0)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
            except (TypeError, ValueError, IndexError):
                pass
    return 60.0
