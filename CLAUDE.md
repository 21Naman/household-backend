# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI backend for a household orchestration agent ("aaj kya banega") that
plans meals, checks inventory/freshness, decides whether an ingredient gap
needs ordering, gates spend approval, and closes the loop through voice/cook
confirmation. Built directly against a `build-map.md` (not in this repo checkout),
phase by phase, ticket by ticket — see the ticket table in `README.md` for
where each numbered ticket landed in the code, and `docs/` for the per-ticket
write-ups (`aws-gate-evidence.md`, `demo-script.md`, `honest-limits.md`,
`submission-alignment.md`).

## Commands

```bash
pip install -r requirements-dev.txt   # app deps + pytest/pytest-cov
cp .env.example .env                  # optional — every setting has a safe default
python -m app.main                    # serve on 127.0.0.1:8000 (docs at /docs)

pytest                                          # full suite (172 passed, 3 skipped without live AWS creds)
pytest tests/test_services.py                   # single file
pytest tests/test_services.py::test_name -v     # single test
pytest --cov=app --cov-report=term-missing      # coverage breakdown
RUN_AWS_INTEGRATION_TESTS=1 pytest tests/test_parity.py   # only with real AWS creds
```

There is no separate lint/format/build step configured (no ruff/black/mypy
config present) — `pytest` is the correctness gate. Migrations run through
Alembic (`alembic/`), configured against `app/database.py`'s engine.

## Architecture: one core, two tracks

The central design rule (Ticket #5): **BUILD IT** (local — Ollama, SQLite,
APScheduler, in-process tool calls) and **SHIP IT** (AWS — Bedrock, DynamoDB,
EventBridge, Lambda) are two implementations of the same four protocols
defined in `app/core/interfaces.py`: `ModelProvider`, `StateStore`,
`EventScheduler`, and the tool provider protocols (`VoiceProvider`,
`LogisticsProvider`, `PaymentProvider`, `CommerceProvider`). Routes and
services never import a concrete provider class directly — everything is
resolved once at startup through `app/core/container.py::build_container()`
and accessed via `app.core.container.get_container()`. When adding a new
capability, add the protocol method first, then implement it on both a
BUILD IT and (if applicable) SHIP IT provider under `app/providers/`.

`app/core/interfaces.py` declares behaviour only — the data shapes the
protocols exchange (`CartQuote`, `DeliveryConfidence`, `PaymentResult`,
`CookBrief`) are defined once next to the code that builds them and imported
there, never re-declared. `tests/test_interfaces.py` checks every provider
the container registers against the protocol it claims to satisfy; that test
exists because the protocols previously drifted out of sync with the
implementations while nothing imported or executed them.

Deciding which concrete classes get wired up happens **only** in
`app/core/container.py` — that's the single place BUILD IT vs SHIP IT is
chosen.

## Deterministic core vs. model calls — the hard boundary

`app/services.py` holds all decision logic that must never be delegated to
an LLM: `effective_freshness` (freshness authority), `compute_ingredient_gap`,
`classify_order_tier` (spend tiers: green/yellow/red), `check_execution_authorized`
(the approval gate), and `consolidate_orders`. These are pure functions (no
I/O, no network) and **must never import from `app.providers`** — this is
mechanically enforced by an import-boundary test in `tests/test_services.py`.
Branch coverage sits at 99% (`pytest --cov=app.services --cov-branch`) — one
partial branch in `effective_freshness` around the recency-window check is
not fully exercised; that gap is real, not a doc error, so don't "round it up"
in future write-ups without actually closing it. A `ModelProvider` may
propose a recipe or explain a preference; it never returns a tier, a price,
or spend authority.

## The gated tool registry — the only path to a tool call

`app/core/registry.py::ToolRegistry` is the single chokepoint through which
any tool (voice, logistics, payments, commerce) gets invoked. A model
response may *request* a tool; it never invokes one directly. Every
`registry.invoke(kind, method, context, ...)` call passes through a
deterministic gate predicate before the underlying provider is touched
(e.g. payments only fires when `tier == GREEN` and a deterministic budget
check already passed). The gate receives the `method` as well as the
context, because some gates care which method is being called: a voice
*reply* is free, a voice *synthesis* spends Gnani credits and is capped per
household per day (`gnani_daily_synthesis_limit`) — a call that spends
vendor credits is spend, and goes through this chokepoint like every other
kind. Refusals raise `ToolCallRefused` and are also logged
to `registry.blocked_calls` for audit — treat this module as security-relevant.
Never call a provider directly from a route handler or from `app/services.py`.

## Approval gate fails closed

`app/services.py::check_execution_authorized` and the stale-approval logic
are deliberately conservative: an unrecognized or stale state resolves to
requiring re-approval, never to silently proceeding. See
`test_stale_approval_refused_when_basket_changed` in `tests/test_services.py`
— this and the unclosed-loop sweep (`app/core/unclosed_sweep.py`, flags
loops that time out rather than letting them silently "succeed") are called
out in the README as the two tickets that matter most.

## Auth

`app/api/deps.py::require_api_key` checks `X-API-Key` against
`HOUSEHOLD_API_KEY` only if that setting is configured. `app/main.py::run()`
refuses to bind to a non-loopback host with no API key set (Ticket #7) — this
is intentional and shouldn't be "fixed" by relaxing the check.

## Settings and mocks

All configuration lives in `app/settings.py` (`Settings`, env prefix
`HOUSEHOLD_`, loaded from `.env`). Every setting has a safe default so the
app boots fully offline with zero credentials — remote/live providers
(Zepto, Pine Labs, Delhivery, Gnani, Google Calendar) are all mock-backed by
default. Note that of the `*_mock_enabled` flags only `zepto_mock_enabled` and
`gnani_mock_enabled` actually select a code path; `commerce_mock_enabled`,
`pinelabs_mock_enabled` and `delhivery_mock_enabled` are reporting-only —
they feed the startup warning but branch nothing, because those rails are
mock-backed unconditionally in `app/core/container.py`. `gnani_mock_enabled`
chooses between `MockVoiceProvider` (the default, which generates a real
playable WAV tone so the offline demo works) and the live
`GnaniBackedVoiceProvider`; the live rail requires a key **and** the mock
flag explicitly off, so a half-configured operator gets the mock plus a
startup warning rather than silent live spend.
`Settings.startup_warnings()` logs, but
never crashes on, missing encryption keys or an unset API key — offline boot
must always succeed.

Pine Labs (`app/providers/payments_pinelabs.py`, ticket #40) is
**intentionally gated, not just untested**: it raises `NotYetValidated` at
construction unless explicitly acknowledged, because the build map marks it
BLOCKED on open research questions. Do not remove or bypass that gate
without being asked.

Gnani (`app/providers/voice_gnani.py`, ticket #41) is gated on **one leg
only**. RQ7 asks whether Gnani's noise robustness generalises from
telephony-grade audio to a phone mic in a running kitchen — a question
about a microphone — so `transcribe()` still raises `GnaniNotYetValidated`
without the acknowledgment flag, while `synthesize()` constructs and runs
freely, because text-to-speech involves no microphone. Do not move that
guard back to `__init__`; `tests/test_blocked_live_rails.py` pins the
boundary in both directions. The API key this project holds is TTS-scoped,
so the STT leg could not run even with the gate lifted.

The TTS request body is written against the full Timbre v2.5 contract, and
its exact JSON is asserted against a stubbed transport in
`tests/test_recipe_audio.py`. **Do not edit that body without updating those
assertions** — two earlier revisions shipped wrong bodies (`Bearer` auth;
then `speed` nested inside `audio_config`, `format` instead of `container`,
and a required `voice` omitted) and nothing caught either, because the tests
stubbed the provider and never looked at the wire. `speed` is top-level.
`audio_config` is required and complete. `voice` is always sent.

The request is **verified live** — `scripts/probe_gnani_tts.py` returns 200
with a well-formed MP3. Two things that probe found and that the vendor docs
get wrong: the real error bodies do **not** match the published
`StandardErrorResponse` shape (`_error_from` handles all three observed
shapes; its tests use captured real bodies), and there **is** a text length
limit despite the docs stating none — 2000 chars works, 2500 returns a 500,
so `gnani_tts_max_chars` is 1500. See `docs/honest-limits.md`.

Spend-tier thresholds, the inventory recency window, and the unclosed-loop
timeout are all named settings (`spend_tier_green_ceiling_inr`,
`spend_tier_red_floor_inr`, `inventory_recency_window_hours`,
`loop_unclosed_timeout_hours`, `delivery_confidence_threshold`,
`delivery_deadline_minutes`, `recipe_min_stocked_ingredient_ratio`,
`unclosed_sweep_interval_seconds`, `gnani_tts_max_chars`,
`gnani_daily_synthesis_limit`, `recipe_audio_ttl_seconds`,
`recipe_audio_cache_max_entries`) — never hardcode these values inline.
The language and voice tables (`gnani_language_map`, `gnani_voice_map`,
`gnani_default_language`) live there too, so a household can be retargeted at
a different voice without a code change, as does the whole Gnani
`audio_config` — built by `Settings.gnani_audio_config()` so the request's
shape has exactly one definition.

Remaining budget has exactly one definition:
`app/services.py::remaining_budget`. The tier classifier, the procurement
consolidator, the recipe affordability check and the figure shown to the
model in its prompt context all call it. Never inline the
`monthly_limit - spent - planned` subtraction at a call site — four copies
of it had already accumulated, which meant the model could be told a
different remaining budget than the gate enforced.

## Recipe generation (v2)

`POST /api/v2/households/{household_id}/loops/{loop_id}/recipe`
(`app/api/routes.py::generate_recipe`, backed by
`app/providers/recipe_model_chain.py::RoundRobinRecipeProvider`) alternates
Groq and Gemini as the initial remote provider, fails over to the other on
an operational error, and only falls back to the local Ollama model if both
remote providers fail. It never persists the raw prompt or generated recipe
to the database. Generated recipes are deterministically rejected (not by
the model) if they exceed the time limit, have fewer than
`recipe_min_stocked_ingredient_ratio` (default 60%) fully stocked
purchasable ingredients, or the cheapest quoted missing basket exceeds
remaining budget; one correction request is allowed before returning `422`.
This is a separate endpoint from the original deterministic `/plan`, which
still makes no model call — but the two share pricing, delivery-scoring,
tiering and approval logic (see "Data access" below), so a change to one of
those shared helpers affects both. Ollama remains the provider for cook
briefs regardless.

## Audio briefing — synthesis happens at fetch time, not at generation

A successful `/recipe` response carries an opaque `audio_id`. **Generating a
recipe does no rewrite and no speech synthesis at all** — a cook who never
presses play costs nothing in model tokens or Gnani credits. Both happen on
`GET /api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}`
(`app/api/routes.py::get_recipe_audio`), and the result is cached so
replaying — or a browser issuing range requests while the cook scrubs the
player — does not re-spend.

The rewrite is a separate model call in `app/core/recipe_briefing.py`
(`RecipeBriefer`), running through the same `RoundRobinRecipeProvider` as
recipe generation so it inherits the Groq/Gemini/Ollama failover. It is a
sibling of `recipe_planner.py`, not an extension of it, and follows the same
propose → deterministically verify → correct once → fail shape. Two rules
the model is not trusted to keep, and which `verify_speakable` enforces:

- **No digits or currency symbols survive into spoken text** (including
  Devanagari digits, which mispronounce identically). One correction is
  allowed, then it degrades to text rather than synthesizing something that
  will mispronounce.
- **Nothing about money, spend tier, or approval state is ever spoken.** The
  cook is told what is missing from the kitchen, because that changes what
  they do; they are not told what it costs or whether anyone approved it.
  Spend authority is a separate human decision on a different channel.
  Nutrition notes are excluded too — they are written for the planner, not
  the person at the stove.

`VoiceProvider.synthesize(text, language, voice)` is deliberately generic:
text in, audio bytes out, nothing recipe-shaped about it. `voice` is
required — Gnani requires it, so this does too.

Language starts from `CookProfile.language` (the cook is the listener) and
goes through `resolve_voiced_language` in `app/providers/language.py`, which
**always** returns a language that can actually be spoken: the cook's own
when it maps to a code with a configured voice, and `gnani_default_language`
(Hinglish, `hi-en`/Poorvi) otherwise. Only four voices are configured, so
most languages take the fallback — see `docs/honest-limits.md`. That is an
explicit named setting, not the model guessing, and it means `audio_id` is
always minted.

The strict `resolve_language` is still there and still returns `None` for an
unknown name; the fallback composes on top rather than replacing it. The
resolved language and voice are stored **on the cache entry** at generation
time, not re-derived at fetch, so a settings change between the POST and the
GET cannot hand the synthesis call a voiceless payload.

`Language.script` keeps the rewrite prompt and the TTS call from disagreeing
about Devanagari vs Latin; `Language.display_name` is what goes *into* the
prompt, because "write in hi-en" is a routing code, not an instruction a
model follows. Hinglish additionally gets explicit code-mixing guidance in
`_LANGUAGE_GUIDANCE` — asked for it plainly, models drift into near-pure
English or transliterate "pressure cooker" into Devanagari.

`app/core/recipe_audio_cache.py` holds all of this in process memory,
bounded by TTL and entry count, never in the database — an MP3 of a recipe
read aloud is the recipe in a lossier container, so persisting one would
break the never-persist invariant in substance. Reads re-check household and
loop even though the id is opaque, because this system has no
cross-household read path anywhere else. The cache and the registry's daily
synthesis counter are both **per-process**; they move to a shared store
together when this is deployed multi-worker (`docs/honest-limits.md`).

## Typed vocabulary

`app/enums.py` is the single source of truth for every status/tier string in
the system (`SpendTier`, `LoopStatus`, `TaskStatus`, `ApprovalStatus`,
`ProcurementPath`, `FreshnessState`, `Language`) — these replaced free-`str` fields
specifically to make typos fail loudly instead of writing a silently
corrupted record. Add new states here, not as raw strings elsewhere.

## Data access

HTTP handlers are split in two: `app/api/routes_crud.py` holds the thin
household-scoped CRUD pass-throughs, and `app/api/routes.py` holds the loop
orchestration — planning, pricing, tiering, approval, execution. Both mount
under `/api` in `app/main.py`, which also serves the demo UI from
`app/static/index.html` — the only static mount in the application, and
mounted *after* every route because a mount at `/` swallows anything
unclaimed before it, including `/health` and `/docs`.

That page now drives the full loop: generate → play → **"I cooked this"** →
`POST /loops/{id}/outcome`. Two things to know before editing it. Everything
lives inside one IIFE and nothing is exported, so new code must go inside it
to reach `esc`, `headers` and `detail`. And `render()` builds a markup string,
assigns it once, then hydrates — `hydrateAudio` and `hydrateCookedPanel` are
separate functions specifically so an early `return` in one cannot skip the
other, which is a bug that existed when the audio guard lived inline. The v1 `/plan` and v2 `/recipe` handlers share
`_quote_gap`, `_score_delivery`, `_classify_tier` and
`_record_approval_if_needed` so the two paths cannot drift apart on pricing,
delivery scoring, thresholds, or when approval is demanded.

All queries are household-scoped through `app/repositories.py` — there is no
cross-household read path. `app/models.py` defines the SQLModel tables;
schema changes go through Alembic migrations in `alembic/versions/`, not
manual `SQLModel.metadata.create_all` edits (that pattern is only used in
test fixtures, e.g. `tests/conftest.py`, against an in-memory SQLite engine).
