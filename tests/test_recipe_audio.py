"""The audio-briefing path: rewrite, gate, cache, chunk, route, contract.

Everything here runs offline. The TTS request body IS asserted, against a
stubbed transport -- that check's absence is what let two wrong request
bodies ship. The error-shape tests use bodies captured from the live API,
not the ones its documentation describes, because those differ.

What these do not cover: the speech-to-text leg, which stays gated on RQ7
and which the TTS-scoped key cannot exercise. See docs/honest-limits.md and
the module docstring in app/providers/voice_gnani.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.recipe_audio_cache import RecipeAudioCache, RecipeAudioEntry
from app.core.recipe_briefing import RecipeBriefer, RecipeBriefingError, Briefing, verify_speakable
from app.core.registry import ToolCallRefused, ToolKind, ToolRegistry
from app.enums import Language
from app.providers.language import resolve_language, voice_for

UTC = timezone.utc


# ---------------------------------------------------------------- cache ----


def _entry(household_id: int = 1, loop_id: int = 10, age_seconds: int = 0) -> RecipeAudioEntry:
    return RecipeAudioEntry(
        household_id=household_id,
        loop_id=loop_id,
        recipe=None,
        missing_ingredients=[],
        language=Language.HI_EN,
        voice="Poorvi",
        skill_level="intermediate",
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def test_cache_read_is_scoped_to_the_household_and_loop_in_the_path():
    """An opaque id is unguessable, but it is not the access control. Every
    other read path in this system is household-scoped and this one is too."""
    cache = RecipeAudioCache(ttl_seconds=1800, max_entries=8)
    audio_id = cache.put(_entry(household_id=1, loop_id=10))

    assert cache.get(audio_id, 1, 10) is not None
    assert cache.get(audio_id, 2, 10) is None
    assert cache.get(audio_id, 1, 99) is None
    assert cache.get("not-a-real-id", 1, 10) is None


def test_cache_is_bounded_by_both_ttl_and_entry_count():
    expired = RecipeAudioCache(ttl_seconds=30, max_entries=8)
    stale_id = expired.put(_entry(age_seconds=120))
    assert expired.get(stale_id, 1, 10) is None

    bounded = RecipeAudioCache(ttl_seconds=1800, max_entries=3)
    for index in range(6):
        bounded.put(_entry(loop_id=index))
    assert len(bounded) <= 3


def test_each_generation_gets_a_distinct_id_so_a_stale_id_cannot_serve_new_audio():
    cache = RecipeAudioCache(ttl_seconds=1800, max_entries=8)
    first = cache.put(_entry())
    second = cache.put(_entry())
    assert first != second


# ------------------------------------------------------------- briefing ----


@pytest.mark.parametrize(
    "text",
    [
        "Rinse 200 grams of rice.",          # ASCII digits
        "दो सौ ग्राम चावल, २०० ग्राम.",        # Devanagari digits
        "The basket costs ₹120.",            # currency
        "Reduce heat by 50%.",               # percent sign
    ],
)
def test_verify_speakable_rejects_anything_that_mispronounces(text):
    assert verify_speakable([text])


def test_verify_speakable_accepts_fully_spelled_out_text():
    assert verify_speakable(["Rinse two hundred grams of rice under running water."]) == []
    assert verify_speakable([]) == ["the briefing was empty"]


def test_chunking_only_ever_splits_between_segments():
    """A chunk boundary must be a step boundary. Cutting mid-sentence is the
    one outcome worth avoiding, so an over-long single segment goes out
    whole and lets the vendor refuse it."""
    briefer = RecipeBriefer(None, max_chars=12)
    chunks = briefer.chunk(Briefing(segments=["aaaa", "bbbb", "cccc", "ddddddddddddddddd"]))
    assert chunks == ["aaaa bbbb", "cccc", "ddddddddddddddddd"]
    assert all(chunk in " ".join(["aaaa", "bbbb", "cccc", "ddddddddddddddddd"]) for chunk in chunks)


class _StubBriefModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_structured(self, prompt, schema=None):
        self.prompts.append(prompt)
        return self.responses.pop(0)


class _Recipe:
    title = "Tomato Dal Rice"
    servings = 2
    prep_minutes = 25
    assumed_pantry_staples = ["salt"]
    steps = ["Rinse the rice.", "Simmer with tomato."]
    nutrition_notes = ["Dal contributes plant protein."]

    class _Ingredient:
        def __init__(self, ingredient, quantity, unit):
            self.ingredient, self.quantity, self.unit = ingredient, quantity, unit

    ingredients = [_Ingredient("Rice", 200, "g"), _Ingredient("Tomato", 2, "count")]


def test_one_correction_is_allowed_before_the_briefing_gives_up():
    model = _StubBriefModel(
        [
            {"segments": ["Rinse 200g of rice."]},
            {"segments": ["Rinse two hundred grams of rice."]},
        ]
    )
    briefing = RecipeBriefer(model, max_chars=900).brief(
        _Recipe(), [], language=Language.HI_EN, skill_level="intermediate"
    )
    assert briefing.segments == ["Rinse two hundred grams of rice."]
    assert len(model.prompts) == 2
    assert "cannot be spoken aloud" in model.prompts[1]


def test_a_briefing_that_stays_unspeakable_degrades_rather_than_synthesizing():
    model = _StubBriefModel([{"segments": ["200g"]}, {"segments": ["still 200g"]}])
    with pytest.raises(RecipeBriefingError):
        RecipeBriefer(model, max_chars=900).brief(
            _Recipe(), [], language=Language.HI_EN, skill_level="intermediate"
        )


def test_the_briefing_prompt_carries_cooking_facts_and_no_spend_facts():
    """The cook is told what is missing, because it changes what they do.
    They are not told what it costs: spend authority is a separate human
    decision on a different channel."""
    briefer = RecipeBriefer(None, max_chars=900)
    prompt = briefer.build_prompt(
        _Recipe(),
        [{"ingredient": "Tomato", "missing_quantity": 2, "unit": "count", "quantity_unknown": False}],
        language=Language.HI_EN,
        skill_level="intermediate",
    )
    assert "Tomato Dal Rice" in prompt
    assert "missing_from_kitchen" in prompt
    assert "Rinse the rice." in prompt
    context_block = prompt.split("RECIPE_CONTEXT:")[1]
    assert "nutrition" not in context_block.lower()
    assert "Dal contributes plant protein" not in prompt
    assert "Never mention prices, budgets, approvals" in prompt
    # The prompt names the language the way a person would, not by its Gnani
    # routing code -- "write in hi-en" is not an instruction a model follows well.
    assert "Hinglish" in prompt and "Latin" in prompt
    assert "hi-en" not in prompt
    # Hinglish specifically needs steering or it collapses into English.
    assert "pressure cooker" in prompt


def test_script_follows_the_language_so_the_prompt_and_the_voice_agree():
    briefer = RecipeBriefer(None, max_chars=900)
    hindi = briefer.build_prompt(_Recipe(), [], language=Language.HI_IN, skill_level="expert")
    assert "Devanagari script" in hindi
    english = briefer.build_prompt(_Recipe(), [], language=Language.EN_IN, skill_level="expert")
    assert "Latin script" in english


# ------------------------------------------------------------- language ----


def test_an_unmapped_language_resolves_to_nothing_rather_than_a_guess():
    mapping = {"hindi": "hi-en", "english": "en-IN"}
    assert resolve_language("Hindi", mapping) is Language.HI_EN
    assert resolve_language("  ENGLISH ", mapping) is Language.EN_IN
    assert resolve_language("Klingon", mapping) is None
    assert resolve_language(None, mapping) is None
    assert resolve_language("hindi", {"hindi": "xx-XX"}) is None
    assert voice_for(Language.TA_IN, {"hi-en": "Poorvi"}) is None


# ------------------------------------------------------------- the gate ----


class _CountingVoice:
    def __init__(self):
        self.calls = 0

    def synthesize(self, *args, **kwargs):
        self.calls += 1
        return b"audio"

    def reply(self, *args, **kwargs):
        return "brief"


def test_synthesis_is_capped_per_household_per_day_at_the_registry_gate():
    """A call that spends vendor credits is spend, so it is refused in the
    same chokepoint that refuses unauthorised payments -- not in the route."""
    registry = ToolRegistry(daily_synthesis_limit=2)
    voice = _CountingVoice()
    registry.register(ToolKind.VOICE, voice)
    context = {"cook_facing": True, "household_id": 1}

    registry.invoke(ToolKind.VOICE, "synthesize", context)
    registry.invoke(ToolKind.VOICE, "synthesize", context)
    with pytest.raises(ToolCallRefused):
        registry.invoke(ToolKind.VOICE, "synthesize", context)

    assert voice.calls == 2
    assert registry.blocked_calls.entries[-1]["tool"] == "voice"
    # A different household has its own budget.
    registry.invoke(ToolKind.VOICE, "synthesize", {"cook_facing": True, "household_id": 2})
    assert voice.calls == 3


def test_the_cap_applies_to_synthesis_only_and_replies_stay_free():
    registry = ToolRegistry(daily_synthesis_limit=1)
    registry.register(ToolKind.VOICE, _CountingVoice())
    registry.invoke(ToolKind.VOICE, "synthesize", {"cook_facing": True, "household_id": 1})
    for _ in range(5):
        assert registry.invoke(ToolKind.VOICE, "reply", {"cook_facing": True}) == "brief"


def test_synthesis_still_requires_a_cook_facing_context():
    registry = ToolRegistry(daily_synthesis_limit=5)
    registry.register(ToolKind.VOICE, _CountingVoice())
    with pytest.raises(ToolCallRefused):
        registry.invoke(ToolKind.VOICE, "synthesize", {"household_id": 1})
    with pytest.raises(ToolCallRefused):
        registry.invoke(ToolKind.VOICE, "synthesize", {"cook_facing": True})


# ---------------------------------------------------------------- routes ----


class _FakeChainProvider:
    """Stands in for RoundRobinRecipeProvider. The same instance serves the
    recipe and, later, the briefing rewrite -- which is the real wiring, and
    is what makes the call count below meaningful."""

    provider_name = "groq"

    def __init__(self, responses):
        self.responses = list(responses)
        self.last_provider_name = None
        self.prompts: list[str] = []

    def generate_structured(self, prompt, schema=None):
        self.prompts.append(prompt)
        self.last_provider_name = self.provider_name
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def generate_correction(self, provider_name, prompt, schema=None):
        return self.generate_structured(prompt, schema)


class _RecordingVoice:
    def __init__(self, error: Exception | None = None):
        self.texts: list[str] = []
        self.languages: list = []
        self.voices: list = []
        self.error = error

    def synthesize(self, text, language, voice=None):
        if self.error is not None:
            raise self.error
        self.texts.append(text)
        self.languages.append(language)
        self.voices.append(voice)
        return b"RIFF-not-really-but-opaque"

    def audio_media_type(self):
        return "audio/wav"

    def reply(self, *args, **kwargs):
        raise AssertionError("reply() is not part of the audio path")


RECIPE_PAYLOAD = {
    "title": "Tomato Dal Rice",
    "servings": 2,
    "prep_minutes": 25,
    "ingredients": [
        {"ingredient": "Rice", "quantity": 200, "unit": "g"},
        {"ingredient": "Moong Dal", "quantity": 150, "unit": "g"},
        {"ingredient": "Tomato", "quantity": 2, "unit": "count"},
    ],
    "assumed_pantry_staples": ["water", "salt"],
    "steps": ["Rinse the rice and dal.", "Simmer with tomato."],
    "nutrition_notes": ["Dal contributes plant protein."],
}

BRIEFING_PAYLOAD = {
    "segments": [
        "Aaj hum Tomato Dal Rice bana rahe hain, do logon ke liye.",
        "Do sau gram chawal aur sava sau gram moong dal nikaal lijiye.",
        "Pehle chawal aur dal ko dho lijiye.",
    ]
}


@pytest.fixture()
def audio_client(api_client_factory):
    with api_client_factory(HOUSEHOLD_ZEPTO_MOCK_ENABLED="true") as (client, _engine):
        yield client


def _install(chain_responses, voice=None):
    from app.core.container import get_container

    container = get_container()
    chain = _FakeChainProvider(chain_responses)
    container.recipe_model_provider = chain
    recording = voice or _RecordingVoice()
    container.voice = recording
    container.registry.register(ToolKind.VOICE, recording)
    return chain, recording


def _household(client, cook_language: str = "Hindi") -> tuple[int, int]:
    household_id = client.post("/api/households", json={"name": "Sharma Home"}).json()["id"]
    client.put(
        f"/api/households/{household_id}/cook-profile",
        json={"name": "Asha", "language": cook_language, "skill_level": "intermediate"},
    )
    client.put(
        f"/api/households/{household_id}/budget",
        json={"monthly_limit": 3000, "spent_amount": 0, "planned_amount": 0},
    )
    for ingredient, quantity, unit in (("Rice", 1000, "g"), ("Moong Dal", 500, "g")):
        client.post(
            f"/api/households/{household_id}/inventory",
            json={"ingredient": ingredient, "quantity": quantity, "unit": unit, "freshness": "fresh"},
        )
    loop_id = client.post(f"/api/households/{household_id}/loops", json={"trigger_type": "manual"}).json()["id"]
    return household_id, loop_id


def _generate(client, household_id, loop_id):
    return client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30, "urgency": "routine"},
    )


def test_generating_a_recipe_does_no_rewrite_and_no_synthesis(audio_client):
    """The whole point of fetch-time synthesis: a cook who never presses
    play costs nothing in model tokens or vendor credits."""
    chain, voice = _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client)

    body = _generate(audio_client, household_id, loop_id).json()

    assert body["audio_id"]
    assert len(chain.prompts) == 1          # the recipe only
    assert voice.texts == []                # nothing synthesized


def test_fetching_the_audio_rewrites_then_synthesizes_and_returns_playable_bytes(audio_client):
    chain, voice = _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.content
    assert len(chain.prompts) == 2          # recipe, then the rewrite
    assert "reading a recipe aloud" in chain.prompts[1]
    # Hindi resolves to the code-mixed voice, and the spoken text is what
    # reached the vendor -- not the JSON.
    assert voice.languages == [Language.HI_EN]
    assert voice.voices == ["Poorvi"]
    assert "Do sau gram chawal" in voice.texts[0]
    assert "prep_minutes" not in voice.texts[0]


def test_replaying_is_served_from_cache_and_never_re_spends(audio_client):
    """Without this, a browser issuing range requests while the cook scrubs
    the player would re-run the rewrite and re-synthesize each time."""
    chain, voice = _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]
    url = f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"

    first = audio_client.get(url)
    second = audio_client.get(url)

    assert first.content == second.content
    assert len(chain.prompts) == 2          # not 3
    assert len(voice.texts) == 1            # not 2


def test_a_language_with_no_voice_falls_back_to_hinglish_rather_than_to_silence(audio_client):
    """`voice` is required by the API and only a few languages have one
    configured, so resolving strictly left most households with no audio at
    all. Hinglish is an explicit, named, operator-editable default -- not the
    model guessing -- and a code-mixed briefing is one an Indian home cook can
    follow."""
    chain, voice = _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client, cook_language="Tamil")

    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]
    assert audio_id, "every household should now be offered audio"

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert response.status_code == 200
    assert voice.languages == [Language.HI_EN]
    assert voice.voices == ["Poorvi"]


def test_a_language_with_its_own_voice_is_not_overridden_by_the_fallback(audio_client):
    _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client, cook_language="English")

    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]
    audio_client.get(f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}")

    from app.core.container import get_container

    assert get_container().voice.languages == [Language.EN_IN]
    assert get_container().voice.voices == ["Kaveri"]


def test_audio_cannot_be_fetched_from_another_household(audio_client):
    _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]
    other_id, _ = _household(audio_client)

    leaked = audio_client.get(
        f"/api/v2/households/{other_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert leaked.status_code == 404


def test_an_expired_or_unknown_handle_is_a_clean_404(audio_client):
    _install([RECIPE_PAYLOAD])
    household_id, loop_id = _household(audio_client)

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/does-not-exist"
    )

    assert response.status_code == 404
    assert "generate the recipe again" in response.json()["detail"]


def test_synthesis_failure_is_a_503_and_audits_the_reason_without_the_recipe(audio_client):
    from sqlmodel import Session, select

    from app.database import engine
    from app.models import AuditEvent

    _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD], voice=_RecordingVoice(error=RuntimeError("credits exhausted")))
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert response.status_code == 503
    assert "text remains available" in response.json()["detail"]
    with Session(engine) as session:
        failures = session.exec(
            select(AuditEvent).where(AuditEvent.event == "recipe_audio_failed")
        ).all()
    assert failures, "a synthesis failure must be auditable"
    detail = failures[-1].detail
    assert "RuntimeError" in detail
    # Routing recipe or briefing text through the audit table would persist
    # the generated recipe by the back door.
    assert "Tomato Dal Rice" not in detail
    assert "chawal" not in detail


def test_an_unspeakable_briefing_degrades_to_503_rather_than_mispronouncing(audio_client):
    _install([RECIPE_PAYLOAD, {"segments": ["Rinse 200g rice."]}, {"segments": ["Still 200g."]}])
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert response.status_code == 503
    assert "mispronounce" in response.json()["detail"]


def test_the_daily_cap_refuses_at_the_route_as_a_403(audio_client):
    from app.core.container import get_container

    _install([RECIPE_PAYLOAD, BRIEFING_PAYLOAD])
    get_container().registry.daily_synthesis_limit = 0
    household_id, loop_id = _household(audio_client)
    audio_id = _generate(audio_client, household_id, loop_id).json()["audio_id"]

    response = audio_client.get(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}"
    )

    assert response.status_code == 403
    assert "daily limit" in response.json()["detail"]


# ------------------------------------------------------------- providers ----


def test_the_offline_mock_produces_a_genuinely_playable_file():
    """Not a byte blob with an audio content-type on it. The demo has to
    actually play, or the mock is hiding a broken UI rather than standing in
    for a vendor."""
    import io
    import wave

    from app.providers.voice_mock import MockVoiceProvider

    provider = MockVoiceProvider(model_provider=None)
    audio = provider.synthesize("Do sau gram chawal dho lijiye.", Language.HI_EN)

    assert provider.audio_media_type() == "audio/wav"
    with wave.open(io.BytesIO(audio), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getnframes() > 0

    with pytest.raises(ValueError):
        provider.synthesize("   ", Language.HI_EN)


def test_mock_audio_length_tracks_the_briefing_length():
    from app.providers.voice_mock import MockVoiceProvider

    provider = MockVoiceProvider(model_provider=None)
    short = provider.synthesize("Chawal dho lijiye.", Language.HI_EN)
    long = provider.synthesize("Chawal dho lijiye. " * 20, Language.HI_EN)
    assert len(long) > len(short)


def test_joining_wav_chunks_keeps_every_chunk_audible():
    """Concatenating WAV files byte-for-byte leaves a header claiming the
    length of the first chunk alone, and players truncate to it. This is the
    chunking path's silent-failure mode."""
    import io
    import wave

    from app.providers.voice import join_audio
    from app.providers.voice_mock import MockVoiceProvider

    provider = MockVoiceProvider(model_provider=None)
    parts = [provider.synthesize(f"Step number {word}." * 4, Language.HI_EN) for word in ("one", "two", "three")]

    joined = join_audio(parts, "audio/wav")

    def frames(blob: bytes) -> int:
        with wave.open(io.BytesIO(blob), "rb") as handle:
            return handle.getnframes()

    assert frames(joined) == sum(frames(part) for part in parts)
    assert join_audio([b"a", b"b"], "audio/mpeg") == b"ab"
    assert join_audio([b"only"], "audio/wav") == b"only"
    with pytest.raises(ValueError):
        join_audio([], "audio/wav")


def test_both_voice_providers_satisfy_the_protocol_they_claim():
    """test_interfaces.py checks whatever the container wired up. The live
    rail is not wired up without credentials, so it is checked here."""
    from app.core.interfaces import VoiceProvider
    from app.providers.voice_gnani import GnaniBackedVoiceProvider, GnaniVoiceProvider
    from app.providers.voice_mock import MockVoiceProvider

    live = GnaniBackedVoiceProvider(None, GnaniVoiceProvider(api_key="x", base_url="https://example.com"))
    assert isinstance(MockVoiceProvider(None), VoiceProvider)
    assert isinstance(live, VoiceProvider)


def test_speech_to_text_is_gone_rather_than_quietly_returning_nothing():
    """An empty transcript is the kind of thing that gets demoed as working."""
    from app.providers.voice_mock import MockVoiceProvider

    with pytest.raises(NotImplementedError):
        MockVoiceProvider(None).transcribe(b"audio")


def test_the_container_picks_the_live_rail_only_when_told_to_twice():
    """A key on its own is not consent to spend: the mock flag has to be
    explicitly off as well, so a half-configured operator gets the mock and
    a startup warning rather than silent live spend."""
    from app.core.container import _build_voice
    from app.providers.voice_gnani import GnaniBackedVoiceProvider
    from app.providers.voice_mock import MockVoiceProvider
    from app.settings import Settings

    def settings(**overrides) -> Settings:
        # _env_file=None matters: without it these read the developer's real
        # .env, so this test passed only while nobody had configured live
        # credentials and started failing the moment somebody did. A test of
        # the selection rule must state its own inputs.
        return Settings(_env_file=None, **overrides)

    assert isinstance(_build_voice(settings(), None), MockVoiceProvider)
    assert isinstance(_build_voice(settings(gnani_api_key="k"), None), MockVoiceProvider)
    assert isinstance(_build_voice(settings(gnani_mock_enabled=False), None), MockVoiceProvider)
    assert isinstance(
        _build_voice(settings(gnani_api_key="k", gnani_mock_enabled=False), None),
        GnaniBackedVoiceProvider,
    )


def test_a_build_with_no_tts_rail_says_so_instead_of_reporting_a_failure():
    """The zero-credential offline build is not broken, and must not look
    broken. LocalVoiceProvider is the base with no synthesis leg at all."""
    from app.providers.voice import LocalVoiceProvider, VoiceSynthesisUnavailable

    provider = LocalVoiceProvider(model_provider=None)
    with pytest.raises(VoiceSynthesisUnavailable):
        provider.synthesize("anything", Language.HI_EN)
    with pytest.raises(VoiceSynthesisUnavailable):
        provider.audio_media_type()


def test_a_model_outage_during_the_rewrite_is_reported_as_a_briefing_failure():
    class _Down:
        def generate_structured(self, prompt, schema=None):
            raise RuntimeError("all providers unavailable")

    with pytest.raises(RecipeBriefingError, match="briefing model was unavailable"):
        RecipeBriefer(_Down(), max_chars=900).brief(
            _Recipe(), [], language=Language.HI_EN, skill_level="intermediate"
        )


# ----------------------------------------------------- the Gnani contract ----
#
# These are the tests whose absence let two wrong request bodies ship. They
# assert the exact JSON that goes on the wire against Gnani's published
# Timbre v2.5 contract, with no network involved. They cannot prove the API
# accepts it -- only a live call does that, see scripts/probe_gnani_tts.py --
# but they do stop a field silently moving or disappearing again.


def _captured_request(**provider_kwargs):
    """Synthesize through a stubbed transport and hand back the sent body."""
    import json

    import httpx

    from app.providers.voice_gnani import GnaniVoiceProvider

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3-audio-bytes", headers={"Content-Type": "audio/mpeg"})

    transport = httpx.MockTransport(handler)
    provider = GnaniVoiceProvider(
        api_key="secret-key",
        base_url="https://api.vachana.ai/api/v1",
        audio_config={"container": "mp3", "sample_rate": 24000, "num_channels": 1,
                      "sample_width": 2, "bitrate": "128k"},
        speed=1.0,
        **provider_kwargs,
    )
    original = httpx.Client

    class _StubClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.Client = _StubClient
    try:
        audio = provider.synthesize("Do sau gram chawal dho lijiye.", Language.HI_EN, voice="Poorvi")
    finally:
        httpx.Client = original
    return sent, audio, provider


def test_the_tts_request_matches_the_published_contract():
    sent, audio, provider = _captured_request()

    assert sent["url"] == "https://api.vachana.ai/api/v1/tts/inference"
    assert sent["headers"]["x-api-key-id"] == "secret-key"
    assert "authorization" not in sent["headers"], "Bearer auth was the first wrong guess"

    body = sent["body"]
    assert body["text"] == "Do sau gram chawal dho lijiye."
    assert body["voice"] == "Poorvi"
    assert body["model"] == "timbre-v2.5"
    assert body["language"] == "hi-en"

    # speed is TOP-LEVEL. Nesting it inside audio_config was the second wrong
    # guess, and would have been a 400 on the first live call.
    assert body["speed"] == 1.0
    assert "speed" not in body["audio_config"]

    # The field is `container`, never `format`.
    assert body["audio_config"]["container"] == "mp3"
    assert "format" not in body["audio_config"]

    # audio_config is required and must be complete.
    assert body["audio_config"]["sample_rate"] == 24000
    assert body["audio_config"]["num_channels"] == 1
    assert body["audio_config"]["sample_width"] == 2

    assert audio == b"ID3-audio-bytes"
    assert provider.audio_media_type() == "audio/mpeg"


def test_voice_is_required_rather_than_quietly_omitted():
    """`voice` is required by the API. The previous code dropped the key
    entirely when no voice was configured, which is a 400 waiting to happen."""
    from app.providers.voice_gnani import GnaniVoiceProvider

    provider = GnaniVoiceProvider(api_key="k", base_url="https://example.com")
    with pytest.raises(ValueError, match="requires a voice"):
        provider.synthesize("kuch bhi", Language.HI_EN, voice=None)


def test_audio_config_sends_bitrate_for_mp3_and_encoding_otherwise():
    """The docs say encoding is not required for mp3 and bitrate applies only
    to mp3. Sending both unconditionally invites a 400 about a field you did
    not think you were setting."""
    from app.settings import Settings

    # _env_file=None so this tests the rule, not the developer's .env.
    mp3 = Settings(_env_file=None, gnani_audio_container="mp3").gnani_audio_config()
    assert mp3["bitrate"] == "128k"
    assert "encoding" not in mp3

    wav = Settings(_env_file=None, gnani_audio_container="wav").gnani_audio_config()
    assert wav["encoding"] == "linear_pcm"
    assert "bitrate" not in wav


def test_an_unsupported_container_is_refused_at_construction():
    from app.providers.voice_gnani import GnaniVoiceProvider

    with pytest.raises(ValueError, match="container"):
        GnaniVoiceProvider(api_key="k", base_url="u", audio_config={"container": "flac"})


# The bodies below are REAL, captured from the live API via
# scripts/probe_gnani_tts.py. Gnani does not emit the StandardErrorResponse
# shape its own documentation describes, and the first version of the parser
# looked only for that shape -- so every real failure came back as
# "type=None message=None", an error reporter that reported nothing.
_DOCUMENTED_BODY = {"success": False, "error": {"type": "INVALID_REQUEST_ERROR",
                                                "message": "Invalid text or audio configuration."}}
_REAL_500_BODY = {"success": False, "status_code": 500,
                  "message": "We are facing technical difficulties. Please try again later."}
_REAL_429_BODY = {"detail": {"error_code": "RATE_LIMITED", "message": "Rate limit exceeded",
                             "status_code": 429}}


def _synthesize_against(status_code, body):
    import httpx

    from app.providers.voice_gnani import GnaniError, GnaniVoiceProvider

    provider = GnaniVoiceProvider(api_key="k", base_url="https://example.com")
    original = httpx.Client

    class _StubClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(status_code, json=body)
            )
            super().__init__(*args, **kwargs)

    httpx.Client = _StubClient
    try:
        with pytest.raises(GnaniError) as caught:
            provider.synthesize("kuch", Language.HI_EN, voice="Poorvi")
    finally:
        httpx.Client = original
    return caught.value


@pytest.mark.parametrize(
    "status_code, body, expected_type, expected_fragment",
    [
        (400, _DOCUMENTED_BODY, "INVALID_REQUEST_ERROR", "Invalid text"),
        (500, _REAL_500_BODY, None, "technical difficulties"),
        (429, _REAL_429_BODY, "RATE_LIMITED", "Rate limit exceeded"),
        (403, _REAL_500_BODY, None, "credits"),
    ],
)
def test_every_error_body_shape_gnani_really_sends_is_parsed(
    status_code, body, expected_type, expected_fragment
):
    """A 403 means the key, its scope or the credit balance; a 429 means back
    off; a 500 means the request broke something server-side. Three different
    responses, so they must be distinguishable rather than all arriving as a
    bare httpx string."""
    error = _synthesize_against(status_code, body)

    assert error.status_code == status_code
    assert error.error_type == expected_type
    assert expected_fragment in str(error)


def test_the_rate_limit_hint_survives_the_undocumented_body_shape():
    """Observed live: the 429 body nests under `detail` and names the field
    `error_code`, not `error.type`. Losing this would turn a back-off signal
    into an unexplained failure."""
    error = _synthesize_against(429, _REAL_429_BODY)
    assert "back off" in str(error)
    assert error.error_type == "RATE_LIMITED"


def test_a_non_json_error_body_still_produces_a_useful_error():
    """A gateway between here and Gnani can return HTML. Parsing must degrade
    to the status code, not mask the real failure with a JSONDecodeError."""
    import httpx

    from app.providers.voice_gnani import GnaniError, GnaniVoiceProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"<html>Bad Gateway</html>")

    provider = GnaniVoiceProvider(api_key="k", base_url="https://example.com")
    original = httpx.Client

    class _StubClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.Client = _StubClient
    try:
        with pytest.raises(GnaniError, match="502"):
            provider.synthesize("kuch", Language.HI_EN, voice="Poorvi")
    finally:
        httpx.Client = original


def test_joining_refuses_formats_that_are_not_actually_joinable():
    """Ogg carries per-file page headers and a serial number; concatenating
    two of them yields something players truncate or reject. A rare path that
    silently produces half a briefing is worse than one that stops."""
    from app.providers.voice import join_audio

    assert join_audio([b"\xff\xfb", b"\xff\xfb"], "audio/mpeg") == b"\xff\xfb\xff\xfb"
    with pytest.raises(ValueError, match="Cannot join"):
        join_audio([b"OggS-a", b"OggS-b"], "audio/ogg")
    # A single chunk never needs joining, so it is returned untouched.
    assert join_audio([b"OggS-only"], "audio/ogg") == b"OggS-only"


def test_the_hinglish_fallback_is_explicit_and_fails_loudly_when_misconfigured():
    from app.providers.language import resolve_voiced_language

    languages = {"hindi": "hi-en", "english": "en-IN", "tamil": "ta-IN"}
    voices = {"hi-en": "Poorvi", "en-IN": "Kaveri"}

    assert resolve_voiced_language("English", languages, voices, "hi-en") == (Language.EN_IN, "Kaveri")
    # Tamil resolves, but has no voice -> fallback rather than silence.
    assert resolve_voiced_language("Tamil", languages, voices, "hi-en") == (Language.HI_EN, "Poorvi")
    assert resolve_voiced_language("Klingon", languages, voices, "hi-en") == (Language.HI_EN, "Poorvi")
    assert resolve_voiced_language(None, languages, voices, "hi-en") == (Language.HI_EN, "Poorvi")

    # A misconfigured default must fail at the first request, not send a
    # voiceless payload.
    with pytest.raises(ValueError, match="not a language Gnani serves"):
        resolve_voiced_language("Tamil", languages, voices, "xx-XX")
    with pytest.raises(ValueError, match="no voice"):
        resolve_voiced_language("Tamil", languages, {"hi-en": "Poorvi"}, "ta-IN")


def test_empty_text_and_empty_audio_bodies_are_both_refused():
    """A 200 carrying zero bytes is not success. Letting it through caches an
    empty file and the cook presses play on silence."""
    import httpx

    from app.providers.voice_gnani import GnaniError, GnaniVoiceProvider

    provider = GnaniVoiceProvider(api_key="k", base_url="https://example.com")
    with pytest.raises(ValueError, match="empty"):
        provider.synthesize("   ", Language.HI_EN, voice="Poorvi")

    original = httpx.Client

    class _StubClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, content=b""))
            super().__init__(*args, **kwargs)

    httpx.Client = _StubClient
    try:
        with pytest.raises(GnaniError, match="empty audio body"):
            provider.synthesize("kuch", Language.HI_EN, voice="Poorvi")
    finally:
        httpx.Client = original


def test_a_network_failure_is_reported_as_a_gnani_error():
    import httpx

    from app.providers.voice_gnani import GnaniError, GnaniVoiceProvider

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    provider = GnaniVoiceProvider(api_key="k", base_url="https://example.com")
    original = httpx.Client

    class _StubClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.Client = _StubClient
    try:
        with pytest.raises(GnaniError, match="TTS failed"):
            provider.synthesize("kuch", Language.HI_EN, voice="Poorvi")
    finally:
        httpx.Client = original
