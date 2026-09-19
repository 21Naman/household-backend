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

pytest                                          # full suite (136 passed, 3 skipped without live AWS creds)
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
mechanically enforced by an import-boundary test in `tests/test_services.py`
and has 100% branch coverage. A `ModelProvider` may propose a recipe or
explain a preference; it never returns a tier, a price, or spend authority.

## The gated tool registry — the only path to a tool call

`app/core/registry.py::ToolRegistry` is the single chokepoint through which
any tool (voice, logistics, payments, commerce) gets invoked. A model
response may *request* a tool; it never invokes one directly. Every
`registry.invoke(kind, method, context, ...)` call passes through a
deterministic gate predicate before the underlying provider is touched
(e.g. payments only fires when `tier == GREEN` and a deterministic budget
check already passed). Refusals raise `ToolCallRefused` and are also logged
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
default. Note that of the `*_mock_enabled` flags only `zepto_mock_enabled`
actually selects a code path; `commerce_mock_enabled`, `pinelabs_mock_enabled`,
`delhivery_mock_enabled` and `gnani_mock_enabled` are reporting-only — they
feed the startup warning but branch nothing, because those rails are
mock-backed unconditionally in `app/core/container.py`.
`Settings.startup_warnings()` logs, but
never crashes on, missing encryption keys or an unset API key — offline boot
must always succeed.

Two live integrations are **intentionally gated, not just untested**:
Pine Labs (`app/providers/payments_pinelabs.py`, ticket #40) and Gnani
(`app/providers/voice_gnani.py`, ticket #41) raise `NotYetValidated` unless
explicitly acknowledged, because the build map marks them BLOCKED on open
research questions. Do not remove or bypass this gate without being asked.

Spend-tier thresholds, the inventory recency window, and the unclosed-loop
timeout are all named settings (`spend_tier_green_ceiling_inr`,
`spend_tier_red_floor_inr`, `inventory_recency_window_hours`,
`loop_unclosed_timeout_hours`, `delivery_confidence_threshold`,
`delivery_deadline_minutes`, `recipe_min_stocked_ingredient_ratio`,
`unclosed_sweep_interval_seconds`) — never hardcode these values inline.

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
the model) if they exceed the time limit, have under 60% fully stocked
purchasable ingredients, or the cheapest quoted missing basket exceeds
remaining budget; one correction request is allowed before returning `422`.
This is separate from the original deterministic `/plan` endpoint, which is
unchanged. Ollama remains the provider for cook briefs regardless.

## Typed vocabulary

`app/enums.py` is the single source of truth for every status/tier string in
the system (`SpendTier`, `LoopStatus`, `TaskStatus`, `ApprovalStatus`,
`ProcurementPath`, `FreshnessState`) — these replaced free-`str` fields
specifically to make typos fail loudly instead of writing a silently
corrupted record. Add new states here, not as raw strings elsewhere.

## Data access

HTTP handlers are split in two: `app/api/routes_crud.py` holds the thin
household-scoped CRUD pass-throughs, and `app/api/routes.py` holds the loop
orchestration — planning, pricing, tiering, approval, execution. Both mount
under `/api` in `app/main.py`. The v1 `/plan` and v2 `/recipe` handlers share
`_quote_gap`, `_score_delivery`, `_classify_tier` and
`_record_approval_if_needed` so the two paths cannot drift apart on pricing,
delivery scoring, thresholds, or when approval is demanded.

All queries are household-scoped through `app/repositories.py` — there is no
cross-household read path. `app/models.py` defines the SQLModel tables;
schema changes go through Alembic migrations in `alembic/versions/`, not
manual `SQLModel.metadata.create_all` edits (that pattern is only used in
test fixtures, e.g. `tests/conftest.py`, against an in-memory SQLite engine).
