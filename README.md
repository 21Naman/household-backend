# Household Orchestration Agent — Backend

Built directly against `build-map.md`, phase by phase, ticket by ticket, no
phase skipped. This README is the entry point; `docs/` carries the
per-ticket write-ups the build map asked for.

## For reviewers

**The API key is in the submission notes.** Paste it into the field at the top
of the page; it is remembered in your browser. Every `/api` route requires it,
and the app refuses to start as a public deployment without one configured —
that refusal is Ticket #7 and it is deliberate.

**A two-minute path through it.** Pick a household from the dropdown — the
left pane shows exactly what the model is told about them, and separately the
budget and kitchen, which the model is *not* told. Each household carries one
deliberately expired preference, struck through and labelled: the system knows
it and does not act on it. Generate a recipe, press **Read it to me** for the
spoken briefing, then **I cooked this** to watch the kitchen decrement. The
event log records every step.

The three households differ on purpose: **Sharma** is well stocked with room
in the budget (the unremarkable path), **Iyer** has ₹350 left and an English-
speaking cook against a Tamil household (spend pressure, and the voice
fallback), **Khan** has a beginner cook and guests coming (register and
occasion).

**What is real and what is simulated.** Being precise about this, because
guessing wrong in either direction misreads the project:

| | |
|---|---|
| **Live external calls** | Groq and Gemini for recipe generation and the spoken rewrite. Gnani for text-to-speech — a real vendor API returning real MP3s, and the only live vendor rail here. |
| **Mock-backed** | Zepto and the second commerce provider (pricing), Pine Labs (payments), Delhivery (logistics). They compute deterministic answers; no money moves and no order is placed. |
| **Deliberately gated** | Pine Labs live mode raises `NotYetValidated` at construction, and Gnani speech-to-**text** refuses without an explicit acknowledgment — both because the build map marks them blocked on open research questions. Text-to-speech is not gated and does run. |

The startup log says the same thing in its own words: *"Active mocks: zepto,
commerce_second_provider, pinelabs, delhivery — do not present these as live
to a judge."*

**Three things that are easy to miss.** The spend gate fails closed and
re-checks at execution time, so approving a basket and then changing it is
refused rather than spent — the demo has a button that triggers exactly that.
`execute-order` takes no amount and no tier from the caller; both come from a
basket the server priced, because accepting them meant the gate was validating
numbers the caller chose. And a recipe deduction across units it cannot
convert is reported, not guessed at.

**What this deployment does not do** is in [docs/honest-limits.md](docs/honest-limits.md):
there is no local model behind the two remote ones here, the database is
ephemeral and re-seeded every six hours, and the audio cache and daily
synthesis cap are per-process.

## Status at a glance

- **235 tests passing, 3 skipped** (the 3 require live AWS credentials this
  environment doesn't have — see below). **100% branch coverage on
  `app/services.py`**, the deterministic decision core.
- **Phases 0–3 (BUILD IT) are fully built, tested, and runnable end to end
  with zero network calls beyond localhost.** This is the demoable system.
- **Phase 4–6 (SHIP IT / AWS) are code-complete and locally validated**
  (CDK synthesizes cleanly; every provider is implemented against its
  documented API) **but not live-deployed** — this container has no AWS
  account, no credentials, and no network route to AWS. See
  `docs/aws-gate-evidence.md` for exactly what that means and what running
  it live would additionally prove.
- **Ticket #40 (Pine Labs live) is intentionally gated**, not just
  untested — it raises `NotYetValidated` unless explicitly acknowledged,
  because the build map marks it BLOCKED on RQ4/RQ5. This is enforced in
  code, not just documentation.
- **Ticket #41 (Gnani) is gated on one leg only.** RQ7 asks whether Gnani's
  noise robustness survives a kitchen via a phone mic — a question about a
  microphone — so speech-to-**text** still refuses without acknowledgment
  while text-to-**speech** runs freely. **TTS is verified live** — the
  request returns 200 with a well-formed MP3, confirmed by
  `scripts/probe_gnani_tts.py`. STT is not, and the API key in use is
  TTS-scoped so it cannot be. `docs/honest-limits.md` says exactly which
  parts of the audio pipeline are tested and which are not.

## Running it

```bash
pip install -r requirements-dev.txt
cp .env.example .env          # optional; every setting has a safe default
python -m app.main            # serves on 127.0.0.1:8000
```

Interactive API docs: `http://127.0.0.1:8000/docs`.

To demo against furnished households rather than an empty database:

```bash
python scripts/seed_demo_households.py            # create anything missing
python scripts/seed_demo_households.py --refresh  # restore existing rows in place
```

## Deploying it

The `Dockerfile` is host-agnostic; `render.yaml` deploys it to Render's free
tier. (It was written for a Hugging Face Space, but Docker Spaces became a
paid feature in July 2026.) Nothing in the image pins a port — Render, Koyeb
and Cloud Run all inject `$PORT`, and `Settings.resolved_port()` reads it
unless `HOUSEHOLD_PORT` is set deliberately.

Four environment secrets are set in the host's dashboard, never in a tracked
file: `HOUSEHOLD_API_KEY` (give reviewers the value; the demo page has a
field for it), plus `HOUSEHOLD_GROQ_API_KEY`, `HOUSEHOLD_GEMINI_API_KEY` and
`HOUSEHOLD_GNANI_API_KEY`. The model keys are not optional in practice:
there is no Ollama in the cloud, so without them recipe generation has no
working provider at all.

On a free tier the process is stopped when idle, which costs a cold start on
the first request and empties the audio cache, the synthesis counter and the
scheduler each time.

**With no API key configured the container refuses to start** (the host's
own marker is enough to make it treat itself as public). That is Ticket #7 working, not a misconfiguration — see
`docs/honest-limits.md` for what else differs in the cloud, including that
the database is ephemeral and the demo data is restored every six hours.

## Testing

```bash
pytest                                          # 242 passed, 3 skipped
pytest --cov=app --cov-report=term-missing      # coverage breakdown
RUN_AWS_INTEGRATION_TESTS=1 pytest tests/test_parity.py   # only in an env with real AWS creds
```

## LLM recipe generation (v2)

`POST /api/v2/households/{household_id}/loops/{loop_id}/recipe` generates one
fresh, response-only recipe. It alternates Groq and Gemini as its initial
remote provider, fails over to the other on an operational error, then uses
the local Ollama model only if both remote providers fail. The existing
deterministic `/plan` endpoint is unchanged. Cook briefs go through the same
chain; when every leg is unreachable they degrade to a templated line and
report `degraded: true` rather than returning a template that reads like a
written brief. Note there is no Ollama leg in a cloud deployment.

Copy the Groq and Gemini placeholders from `.env.example` into `.env`. No
new Python package is required: the adapters use the already-required
`httpx` library directly. The route constructs the prompt from household
state but never writes the raw prompt or generated recipe to the database. It
then deterministically rejects recipes that exceed the time limit, have under
60% fully stocked purchasable ingredients, or whose cheapest quoted missing
basket exceeds the remaining budget. One correction request is permitted
before it returns `422` with the unmet constraints.

## Audio briefing

A successful `/recipe` response carries an opaque `audio_id`. Fetching
`GET /api/v2/households/{household_id}/loops/{loop_id}/recipe-audio/{audio_id}`
rewrites the recipe into spoken instructions in the cook's language and
synthesizes speech, returning playable audio. **Nothing is rewritten or
synthesized until that fetch happens**, so a cook who never presses play
costs nothing; the result is then cached in memory (never the database) so
replays are free.

The briefing is written in **Hinglish** by default — code-mixed Hindi and
English in Latin script, the way an Indian kitchen actually sounds — using
Gnani's `hi-en` voice. A cook whose profile language has its own configured
voice keeps it (English gets `en-IN`); everyone else falls back to Hinglish,
so every recipe is offered audio.

Quantities are spelled out in words and the text is checked deterministically
for stray digits or currency symbols before it reaches the speech engine.
Missing ingredients are spoken; prices, spend tiers and approval state never
are.

Run `python -m app.main` and open <http://127.0.0.1:8000/> for a demo UI that
drives the **whole loop** — generate a recipe, read it as cards, press play,
then "I cooked this" to close it. Closing writes a `DishHistory` row, deducts
what was used from the kitchen, and moves the loop to `completed` so the
unclosed sweep stops watching it. The panel warns before submitting when a
consumed ingredient will not deduct cleanly — the name matches no lot, the
units disagree, or the amount exceeds stock — because the backend does all
three silently.

Without Gnani credentials it uses an offline mock that produces a real,
playable placeholder tone, so the pipeline is demoable with zero
credentials.

Populate a database to demo against with
`python scripts/seed_demo_households.py` — three households with members,
cook profiles, budgets, inventory, preference signals, leftovers and history,
all dated relative to today.

## Where each build-map ticket landed

| Ticket | What it is | File(s) |
|---|---|---|
| #1 | Planning-layer audit | This file + `app/api/routes.py` docstring — finding: **absent**, built fresh |
| #2 | Typed state vocabulary | `app/enums.py` |
| #3 | Managed migrations | `alembic/`, `app/database.py` |
| #4 | Household-scoped data access | `app/repositories.py` |
| #5 | Core interfaces + gated registry | `app/core/interfaces.py`, `app/core/registry.py` |
| #6 | Rail configuration surface | `app/settings.py` |
| #7 | Minimal auth gate | `app/api/deps.py`, `app/main.py::run` |
| #8 | Checkpoint: foundation holds | `tests/test_repositories.py`, `tests/test_registry.py` |
| #9 | Freshness authority rule | `app/services.py::effective_freshness` |
| #10 | Ingredient-gap calculator | `app/services.py::compute_ingredient_gap` |
| #11 | Spend-tier classifier | `app/services.py::classify_order_tier` |
| #12 | Approval gate + state machine | `app/services.py::check_execution_authorized` |
| #13 | Deterministic-logic test suite | `tests/test_services.py` (100% coverage, import-boundary test) |
| #14 | Checkpoint: refuses correctly | same file, stale-approval + red-refusal tests |
| #15 | LocalModelProvider | `app/providers/ollama.py` |
| #16 | LocalStateStore | `app/core/state_store.py` |
| #17 | LocalScheduler | `app/core/scheduler.py` |
| #18 | Mock tiered payment authorizer | `app/providers/payments_mock.py` |
| #19 | Mock logistics / delivery-confidence | `app/providers/logistics_mock.py` |
| #20 | Second commerce provider + typed payloads | `app/providers/commerce_mock.py`, `app/providers/zepto_mcp.py`, `app/schemas.py` |
| #21 | Budget-aware consolidation | `app/services.py::consolidate_orders` |
| #22 | Checkpoint: full local loop | `tests/test_checkpoint_p2_local_loop.py` |
| #23 | Structured short-term context | `app/models.py::MealLoopRecord.guest_count/occasion` |
| #24 | Unclosed-loop detection | `app/core/unclosed_sweep.py`, `tests/test_unclosed_sweep.py` |
| #25 | Cook reply leg | `app/providers/voice.py` |
| #25a | Audio briefing (rewrite → TTS → UI) | `app/core/recipe_briefing.py`, `app/core/recipe_audio_cache.py`, `app/providers/voice_mock.py`, `app/static/index.html` |
| #28a | Loop closure from the UI | `app/static/index.html` ("I cooked this"), `app/schemas.py::ConsumedItem`, `app/api/routes.py::capture_outcome` |
| #26 | Approval reasons | `app/schemas.py::ApprovalDecision.reason` |
| #27 | Agent-decision tests over seed scenarios | `app/seed.py`, `tests/test_agent_decisions.py` |
| #28 | End-to-end loop closure | `tests/test_loop_e2e.py` (includes the mid-crash transaction test) |
| #29 | Checkpoint: BUILD IT is demoable | all of the above, green |
| #30 | AWS gate | `docs/aws-gate-evidence.md` |
| #31 | Secrets Manager | `infra/cdk/secrets_stack.py` (provisions the secrets). The runtime resolver that originally lived at `app/providers/secrets.py` was removed by a complexity audit — it had no callers and no test coverage; nothing in the app actually read a secret through it. |
| #32 | DynamoDBStateStore | `app/providers/dynamodb_state.py` |
| #33 | BedrockModelProvider | `app/providers/bedrock_model.py` |
| #34 | Lambda tool execution + IAM | `lambdas/handlers.py`, `infra/cdk/tools_stack.py` |
| #35 | EventBridge triggers | `app/providers/eventbridge_scheduler.py`, `infra/cdk/events_stack.py` |
| #36 | Observability + weekly reflection | `app/providers/observability.py`, `tests/test_observability.py` |
| #37 | Parity suite | `tests/test_parity.py` (skipped without live AWS) |
| #38 | Calendar scope verification | `app/providers/google_calendar.py`, `app/settings.py::google_calendar_scopes` |
| #39 | Delhivery Maps (live) | `app/providers/logistics_delhivery.py` |
| #40 | Pine Labs (live, **BLOCKED**) | `app/providers/payments_pinelabs.py` |
| #41 | Gnani (TTS live-capable, STT **BLOCKED** on RQ7) | `app/providers/voice_gnani.py` |
| #42 | CDK infrastructure | `infra/cdk/` (synthesizes cleanly; see `docs/aws-gate-evidence.md`) |
| #43 | Staging deploy + gate evidence | `docs/aws-gate-evidence.md` |
| #44 | Demo beats | `docs/demo-script.md` |
| #45 | Honest-limits doc | `docs/honest-limits.md` |
| #46 | Submission alignment | `docs/submission-alignment.md` |

## Three tickets that matter most if you only check three

Per the build map's own closing note: **#12** (the approval gate fails
closed, not open — see `test_stale_approval_refused_when_basket_changed`),
**#24** (the loop can never silently claim success — see
`test_backdated_unconfirmed_loop_is_flagged_unclosed`), and **#1** (you
now actually know what the planner does, because it's this codebase).
