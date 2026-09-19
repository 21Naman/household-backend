from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.providers.gemini_model import GeminiModelProvider
from app.providers.groq_model import GroqModelProvider
from app.providers.model_failures import RecipeProviderOperationalError
from app.providers.recipe_model_chain import RoundRobinRecipeProvider
from app.providers.remote_model import RemoteRecipeModelProvider, retry_after_from_headers


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}


class FakeRemote:
    def __init__(self, name: str, results: list[dict | Exception], *, available: bool = True):
        self.provider_name = name
        self.results = list(results)
        self.available = available
        self.calls = 0
        self.marked: list[RecipeProviderOperationalError] = []

    def is_available(self) -> bool:
        return self.available

    def generate_structured(self, prompt: str, schema=None):
        self.calls += 1
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def mark_operational_failure(self, error: RecipeProviderOperationalError) -> None:
        self.marked.append(error)
        if error.retry_after_seconds is not None:
            self.available = False

    def health(self):
        return type("Health", (), {"status": "available" if self.available else "cooldown", "detail": None})()


class FakeFallback:
    def __init__(self, result: dict | Exception):
        self.result = result
        self.calls = 0

    def generate_structured(self, prompt: str, schema=None):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def health(self):
        return type("Health", (), {"status": "available", "detail": None})()


def test_remote_providers_round_robin_and_never_use_ollama_on_success():
    groq = FakeRemote("groq", [{"title": "first"}])
    gemini = FakeRemote("gemini", [{"title": "second"}])
    fallback = FakeFallback({"title": "local"})
    chain = RoundRobinRecipeProvider(groq=groq, gemini=gemini, fallback=fallback)

    assert chain.generate_structured("one", SCHEMA) == {"title": "first"}
    assert chain.last_provider_name == "groq"
    assert chain.generate_structured("two", SCHEMA) == {"title": "second"}
    assert chain.last_provider_name == "gemini"
    assert fallback.calls == 0


def test_remote_failure_fails_over_and_429_is_skipped_on_later_request():
    groq = FakeRemote("groq", [RecipeProviderOperationalError("quota", retry_after_seconds=60)])
    gemini = FakeRemote("gemini", [{"title": "remote"}, {"title": "remote again"}])
    fallback = FakeFallback({"title": "local"})
    chain = RoundRobinRecipeProvider(groq=groq, gemini=gemini, fallback=fallback)

    assert chain.generate_structured("one", SCHEMA) == {"title": "remote"}
    assert groq.calls == 1
    assert groq.marked[0].retry_after_seconds == 60
    assert chain.generate_structured("two", SCHEMA) == {"title": "remote again"}
    assert groq.calls == 1
    assert fallback.calls == 0


def test_missing_remote_keys_skip_to_ollama_fallback():
    groq = GroqModelProvider(None, "model", 1)
    gemini = GeminiModelProvider(None, "model", 1)
    fallback = FakeFallback({"title": "local"})
    chain = RoundRobinRecipeProvider(groq=groq, gemini=gemini, fallback=fallback)

    assert chain.generate_structured("recipe", SCHEMA) == {"title": "local"}
    assert chain.last_provider_name == "ollama"
    assert fallback.calls == 1


def test_both_remote_operational_failures_fall_back_to_ollama():
    groq = FakeRemote("groq", [RecipeProviderOperationalError("connection failed")])
    gemini = FakeRemote("gemini", [RecipeProviderOperationalError("provider refused")])
    fallback = FakeFallback({"title": "local"})
    chain = RoundRobinRecipeProvider(groq=groq, gemini=gemini, fallback=fallback)

    assert chain.generate_structured("recipe", SCHEMA) == {"title": "local"}
    assert groq.calls == 1
    assert gemini.calls == 1
    assert fallback.calls == 1
    assert chain.last_provider_name == "ollama"


def test_cooldown_uses_retry_after_or_sixty_seconds():
    provider = RemoteRecipeModelProvider("key", "model", 1)
    assert provider.is_available()
    provider.mark_operational_failure(RecipeProviderOperationalError("rate limited", retry_after_seconds=0.1))
    assert not provider.is_available()
    assert retry_after_from_headers({"Retry-After": "12"}) == 12
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5), usegmt=True)
    assert retry_after_from_headers({"Retry-After": future}) > 0
    assert retry_after_from_headers({}) == 60


class StubResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self.payload


class StubClient:
    response: StubResponse
    requests: list[dict] = []

    def __init__(self, timeout: float):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, **kwargs):
        self.requests.append({"url": url, **kwargs})
        return self.response


def test_groq_and_gemini_parse_structured_json_with_direct_http(monkeypatch):
    # Both providers share one HTTP round trip in remote_model, so that is
    # where the transport is stubbed for either of them.
    import app.providers.remote_model as remote_module

    monkeypatch.setattr(remote_module.httpx, "Client", StubClient)
    StubClient.requests = []
    StubClient.response = StubResponse({"choices": [{"message": {"content": json.dumps({"title": "Upma"})}}]})
    assert GroqModelProvider("groq-key", "model", 3).generate_structured("prompt", SCHEMA) == {"title": "Upma"}
    groq_request = StubClient.requests[-1]
    assert groq_request["headers"]["Authorization"] == "Bearer groq-key"
    assert groq_request["json"]["response_format"]["type"] == "json_schema"

    StubClient.response = StubResponse({"output_text": json.dumps({"title": "Dal"})})
    assert GeminiModelProvider("gemini-key", "model", 3).generate_structured("prompt", SCHEMA) == {"title": "Dal"}
    gemini_request = StubClient.requests[-1]
    assert gemini_request["headers"]["x-goog-api-key"] == "gemini-key"
    assert gemini_request["json"]["response_format"]["mime_type"] == "application/json"


def test_invalid_remote_structured_output_is_an_operational_failure(monkeypatch):
    import app.providers.remote_model as remote_module

    monkeypatch.setattr(remote_module.httpx, "Client", StubClient)
    StubClient.response = StubResponse({"choices": [{"message": {"content": "not json"}}]})
    with pytest.raises(RecipeProviderOperationalError, match="invalid structured output"):
        GroqModelProvider("key", "model", 3).generate_structured("prompt", SCHEMA)
