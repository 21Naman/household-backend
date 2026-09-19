from __future__ import annotations

import pytest

from app.providers.json_schema import validate_against_schema
from app.providers.ollama import validate_structured_json
from app.providers.whisper import language_mismatch
from app.settings import Settings


# ---------------------------------------------------------------------------
# ollama.py -- schema validation edge cases
# ---------------------------------------------------------------------------

def test_validate_structured_json_rejects_non_dict_payload():
    with pytest.raises(ValueError):
        validate_structured_json("not a dict")


def test_validate_structured_json_rejects_missing_response_field():
    with pytest.raises(ValueError):
        validate_structured_json({"no_response_key": True})


def test_validate_structured_json_rejects_invalid_json_in_response():
    with pytest.raises(ValueError):
        validate_structured_json({"response": "{not valid json"})


def test_validate_structured_json_rejects_non_object_decoded_response():
    with pytest.raises(ValueError):
        validate_structured_json({"response": "[1, 2, 3]"})


def test_validate_against_schema_missing_required_field():
    with pytest.raises(ValueError):
        validate_against_schema({}, {"type": "object", "required": ["items"]})


def test_validate_against_schema_wrong_array_type():
    with pytest.raises(ValueError):
        validate_against_schema("not a list", {"type": "array"})


def test_validate_against_schema_exceeds_max_items():
    with pytest.raises(ValueError):
        validate_against_schema([1, 2, 3], {"type": "array", "maxItems": 2})


def test_validate_against_schema_wrong_primitive_type():
    with pytest.raises(ValueError):
        validate_against_schema("a string", {"type": "number"})


def test_validate_against_schema_nullable_union_accepts_null():
    validate_against_schema(None, {"type": ["string", "null"]})  # should not raise


def test_validate_against_schema_nullable_union_rejects_wrong_type():
    with pytest.raises(ValueError):
        validate_against_schema(5, {"type": ["string", "null"]})


def test_generate_structured_end_to_end_with_valid_schema(monkeypatch):
    """Exercises OllamaProvider.generate_structured's happy path without a
    live Ollama server, by monkeypatching httpx.Client.post."""
    from app.providers.ollama import OllamaProvider

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": '{"items": []}'}

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            return _FakeResponse()

    monkeypatch.setattr("httpx.Client", lambda timeout: _FakeClient())
    provider = OllamaProvider("http://fake", "test-model")
    schema = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}
    result = provider.generate_structured("prompt", schema=schema)
    assert result == {"items": []}


# ---------------------------------------------------------------------------
# whisper.py -- language_mismatch helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "detected,expected,mismatch",
    [
        ("ta", "Tamil", False),
        ("en", "Tamil", True),
        ("unknown", "Tamil", False),  # nothing to compare against
        ("hi", "Klingon", False),  # unrecognized expected language never raises a mismatch
        ("", "Tamil", False),
    ],
)
def test_language_mismatch_cases(detected, expected, mismatch):
    assert language_mismatch(detected, expected) is mismatch


# ---------------------------------------------------------------------------
# vision.py -- input validation and failure fallback, no live model needed
# ---------------------------------------------------------------------------

def test_vision_extract_inventory_rejects_empty_image():
    from app.providers.vision import VisionProvider

    provider = VisionProvider("http://fake", "test-model")
    with pytest.raises(ValueError):
        provider.extract_inventory(b"")


def test_vision_extract_inventory_wraps_http_errors(monkeypatch):
    from app.providers.vision import VisionProvider
    import httpx

    class _FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("httpx.Client", lambda timeout: _FailingClient())
    provider = VisionProvider("http://fake", "test-model")
    with pytest.raises(RuntimeError, match="unavailable"):
        provider.extract_inventory(b"fake-image-bytes")


# ---------------------------------------------------------------------------
# google_calendar.py -- config validation without a live OAuth flow
# ---------------------------------------------------------------------------

def test_google_calendar_requires_full_config():
    from app.providers.google_calendar import GoogleCalendarError, GoogleCalendarProvider

    settings = Settings(_env_file=None, google_client_id=None)
    provider = GoogleCalendarProvider(settings)
    with pytest.raises(GoogleCalendarError):
        provider.authorization_url("state123")


def test_google_calendar_authorization_url_uses_narrowed_scopes():
    from app.providers.google_calendar import GoogleCalendarProvider
    from cryptography.fernet import Fernet

    settings = Settings(
        _env_file=None,
        google_client_id="cid",
        google_client_secret="secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )
    provider = GoogleCalendarProvider(settings)
    url = provider.authorization_url("state123")
    assert "calendar.events.owned" not in url  # Ticket #38: the unverified scope is gone
    assert "calendar.freebusy" in url or "calendar.events.readonly" in url


def test_google_calendar_invalid_encryption_key_raises():
    from app.providers.google_calendar import GoogleCalendarError, GoogleCalendarProvider

    settings = Settings(_env_file=None, google_client_id="cid", google_client_secret="s", google_token_encryption_key="not-a-valid-fernet-key")
    provider = GoogleCalendarProvider(settings)
    with pytest.raises(GoogleCalendarError):
        provider.authorization_url("state123")
