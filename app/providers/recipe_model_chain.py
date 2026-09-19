"""Round-robin Groq/Gemini recipe generation with local Ollama fallback."""
from __future__ import annotations

from threading import Lock
from typing import Any

from app.providers.model_failures import RecipeProviderOperationalError


class RoundRobinRecipeProvider:
    """Use alternating remote primaries without letting a remote model decide
    policy. Provider failures are handled here; recipe feasibility is handled
    by the planner after structured output has been received.
    """

    def __init__(self, *, groq, gemini, fallback) -> None:
        self._remote = [groq, gemini]
        self._fallback = fallback
        self._next_remote_index = 0
        self._lock = Lock()
        self.last_provider_name: str | None = None

    def _next_order(self) -> list:
        with self._lock:
            first = self._next_remote_index
            self._next_remote_index = (self._next_remote_index + 1) % len(self._remote)
        return [self._remote[first], self._remote[(first + 1) % len(self._remote)]]

    def generate_structured(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        return self._generate(self._next_order(), prompt, schema)

    def generate_correction(self, provider_name: str | None, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        """Keep a semantic correction on the original provider when possible.

        It will still fail over to the other remote provider and then Ollama
        if that correction request has an operational failure.
        """
        preferred = next((provider for provider in self._remote if provider.provider_name == provider_name), None)
        if preferred is None:
            return self.generate_structured(prompt, schema)
        ordered = [preferred, *(provider for provider in self._remote if provider is not preferred)]
        return self._generate(ordered, prompt, schema)

    def _generate(self, remotes: list, prompt: str, schema: dict | None) -> dict[str, Any]:
        failures: list[str] = []
        for provider in remotes:
            if not provider.is_available():
                failures.append(f"{provider.provider_name}: unavailable")
                continue
            try:
                result = provider.generate_structured(prompt, schema)
                self.last_provider_name = provider.provider_name
                return result
            except RecipeProviderOperationalError as exc:
                provider.mark_operational_failure(exc)
                failures.append(f"{provider.provider_name}: unavailable")

        try:
            result = self._fallback.generate_structured(prompt, schema)
            self.last_provider_name = "ollama"
            return result
        except Exception as exc:
            failures.append("ollama: unavailable")
            raise RecipeProviderOperationalError("No recipe-generation provider is currently available") from exc

    def health(self):
        remote_states = [provider.health().status for provider in self._remote]
        if any(state == "available" for state in remote_states):
            return type("RecipeProviderHealth", (), {"status": "available", "detail": None})()
        fallback = self._fallback.health()
        return type("RecipeProviderHealth", (), {"status": fallback.status, "detail": fallback.detail})()
