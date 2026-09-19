"""Ticket #15 — LocalModelProvider (ModelProvider over Ollama).

Changed from the original upload:
  - generate_structured(prompt, schema=None) now accepts an explicit JSON
    schema instead of hardcoding freeform format="json". vision.py already
    demonstrated this stricter pattern with INVENTORY_SCHEMA; the other two
    sanctioned LLM uses (preference arbitration, cook dialogue) deserve the
    same rigor.
  - validate_structured_json is now a module-level function, not a method,
    so vision.py no longer needs to construct a throwaway OllamaProvider
    just to call it. The schema checker it delegates to now lives in
    app/providers/json_schema.py, since Groq and Gemini use it too.
  - inline __import__("json") replaced with a top-level import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.json_schema import validate_against_schema


@dataclass(frozen=True)
class OllamaHealth:
    status: str
    model: str
    detail: str | None = None


def validate_structured_json(payload: Any, schema: dict | None = None) -> dict[str, Any]:
    """Validate the response shape used by structured Ollama calls. If a
    JSON schema is supplied, the decoded response is checked against it
    with a minimal validator (required keys + type checks) — enough to
    catch a model ignoring the schema without pulling in a full JSON-Schema
    library for this. Raises ValueError on any violation."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object from Ollama")
    response = payload.get("response")
    if not isinstance(response, str):
        raise ValueError("Ollama JSON response must include a string response field")
    try:
        decoded = json.loads(response)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ollama response field does not contain valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Structured Ollama output must be a JSON object")

    if schema:
        validate_against_schema(decoded, schema)
    return decoded


class OllamaProvider:
    """Local-only ModelProvider implementation.

    It serves extraction, preference arbitration, and cook dialogue, and is
    the final recipe-generation fallback only after both remote providers
    fail. It never produces a tier, price, procurement decision, or authority
    to spend money.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def health(self) -> OllamaHealth:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            return OllamaHealth("unavailable", self.model, str(exc))
        except ValueError:
            return OllamaHealth("invalid_response", self.model, "Ollama returned invalid JSON")

        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return OllamaHealth("invalid_response", self.model, "Ollama response lacks a models list")
        available_models = {
            entry.get("name") for entry in payload["models"] if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }
        if self.model not in available_models:
            return OllamaHealth("model_unavailable", self.model, f"Pull the local model with: ollama pull {self.model}")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": 'Return exactly this JSON object: {"health":"ok"}',
                        "format": "json",
                        "think": False,
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                validate_structured_json(response.json())
        except httpx.HTTPError as exc:
            return OllamaHealth("unavailable", self.model, f"Model verification failed: {exc}")
        except ValueError as exc:
            return OllamaHealth("invalid_response", self.model, str(exc))
        return OllamaHealth("available", self.model)

    def generate_structured(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        """Ticket #15: schema-constrained by default when a schema is
        given. Falls back to freeform JSON mode only when schema is None,
        for call sites that genuinely don't have one yet."""
        request_format: Any = schema if schema else "json"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "format": request_format,
                    "think": False,
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            return validate_structured_json(response.json(), schema=schema)
