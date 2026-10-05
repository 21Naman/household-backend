"""Cook briefs on the shared model chain, and the end of a bare except.

`LocalVoiceProvider.reply` used to wrap its model call in `except Exception`
and return a templated string on anything at all. That is correct for an
unreachable model -- the zero-credential offline build is a supported state --
and wrong for a model that answered with the wrong shape, which is a bug being
returned as a 200 that reads exactly like a real brief.
"""
from __future__ import annotations

import pytest

from app.providers.model_failures import RecipeProviderOperationalError
from app.providers.voice import LocalVoiceProvider


class _DownChain:
    """Every leg unconfigured or unreachable -- what a cloud deploy with no
    keys, and CI, both look like."""

    def generate_structured(self, prompt, schema=None):
        raise RecipeProviderOperationalError("no provider is configured")


class _MalformedModel:
    """Answered, but not with what was asked for."""

    def generate_structured(self, prompt, schema=None):
        return {"not_the_key_we_asked_for": "hello"}


class _WorkingModel:
    def generate_structured(self, prompt, schema=None):
        return {"brief": "Chawal ko dho lo, phir 20 minute pakao."}


def _reply(provider):
    return LocalVoiceProvider(provider).reply(
        dish_name="Khichdi", instructions="less oil", language="Hindi", skill_level="beginner"
    )


def test_a_working_model_is_not_marked_degraded():
    brief = _reply(_WorkingModel())
    assert brief.degraded is False
    assert "Chawal" in brief.text


def test_an_unreachable_chain_degrades_and_says_so():
    """Text stays the guaranteed channel, but the caller is told it is a
    template rather than left to assume it is a written brief."""
    brief = _reply(_DownChain())
    assert brief.degraded is True
    assert brief.text == "[Hindi, beginner] Khichdi: less oil"


def test_no_model_provider_still_degrades_rather_than_raising():
    """Several callers construct this with None on purpose."""
    brief = _reply(None)
    assert brief.degraded is True
    assert brief.text.startswith("[Hindi, beginner]")


def test_a_malformed_model_response_is_not_silently_templated():
    """The case the bare `except Exception` was hiding.

    A model that answers with the wrong shape is a bug. Returning a template
    for it produces a 200 that is indistinguishable from a real brief, which
    is exactly the failure mode this file's own transcribe() docstring warns
    about: the kind of thing that gets demoed as working.
    """
    with pytest.raises(KeyError):
        _reply(_MalformedModel())


def test_the_cook_brief_endpoint_reports_degradation(api_client):
    """CI has no model of any kind, so this exercises the degraded path end
    to end and pins that the flag reaches the response and the audit row."""
    client, _ = api_client
    hid = client.post("/api/households", json={"name": "Brief HH"}).json()["id"]
    client.put(
        f"/api/households/{hid}/cook-profile",
        json={"name": "Asha", "language": "Hindi", "skill_level": "beginner"},
    )
    loop = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()

    response = client.post(
        f"/api/households/{hid}/loops/{loop['id']}/cook-brief",
        params={"dish_name": "Khichdi", "instructions": "less oil"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "degraded" in body
    events = client.get(f"/api/households/{hid}/audit").json()
    sent = [e for e in events if e["event"] == "cook_brief_sent"]
    assert sent and "degraded=" in sent[0]["detail"]
