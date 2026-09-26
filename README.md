# Aaj Kya Banega — Household Orchestration Agent

An AI agent that runs the daily Indian meal decision end to end: it plans a dish
against what is actually in your kitchen, works out what is missing, decides
whether that gap is worth ordering, **asks a human before spending money**, reads
the recipe aloud to the cook in their own language, and closes the loop when
someone confirms they cooked it.

---

# ▶ RUN IT RIGHT NOW — NOTHING TO INSTALL

## Step 1 — Open the live app

### **https://aaj-kya-banega-zhml.onrender.com**

> ⏳ **The first load takes about 25 seconds.** This is a free-tier server waking
> up from sleep. It is not broken — wait for it. Every click after that is instant.

## Step 2 — Paste the API key

At the top of the page there is a field labelled **API key**. Paste this in:

```
FV0PIhC5gKLushnUtnq1xcQ6e6lg0eHd
```

Your browser remembers it, so you only do this once. Every `/api` route requires
it — without it the app returns `401`, and it refuses to even start as a public
deployment with no key configured. That is the auth gate (Ticket #7) working.

## Step 3 — Use it

1. **Pick a household** from the dropdown — try **Iyer Household** first.
2. **Read the left pane.** That is everything the AI is told about this family.
   Below it, separately labelled, is the budget and kitchen the AI is *not* told.
   One preference is struck through and marked **expired** — the system knows
   about it and deliberately does not act on it.
3. **Press "Generate".** A live LLM writes a recipe; deterministic code then
   prices it, checks it against the budget, and assigns a spend tier. Leftovers
   the recipe reuses are listed separately and are never priced or bought.
4. **If ingredients are missing, press "Build cart".** It fills an Instamart cart
   with the first in-stock match for each one. There is no checkout button,
   because there is no checkout path — the tool gate refuses every order and
   payment method.
5. **Press "Read it to me".** That is a real voice from a real text-to-speech
   vendor, speaking Hinglish. Turn your volume up.
6. **If a spend approval appears, approve it, then execute.** Now go back and
   **generate a second recipe**, and try to execute again — you will get a
   **403 refusal**, because the basket changed after you approved it. That
   refusal is the single best thing in this project.
7. **Press "I cooked this".** Watch the kitchen inventory decrement in the left pane.

**Three households, three different things to see:**

| Household | What it demonstrates |
|---|---|
| **Sharma** | Well stocked, budget headroom — the unremarkable happy path |
| **Iyer** | ₹350 left this month, English-speaking cook in a Tamil home — spend pressure and voice fallback |
| **Khan** | Beginner cook, guests coming — how skill and occasion change the output |

---

📖 **Judges: the full guided tour is [`JUDGES.md`](JUDGES.md)** — what runs live,
what is mock-backed, what is deliberately gated, and the engineering decisions
worth your time. Everything below here is reference material.

---

## What is real and what is simulated

Being precise about this, because guessing wrong in either direction misreads
the project.

| | |
|---|---|
| **Live external calls** | Groq (`openai/gpt-oss-120b`) and Gemini (`gemini-3.8-flash`) for recipe generation and the spoken rewrite. Gnani Timbre v2.5 for text-to-speech — a real vendor API returning real MP3s. |
| **Mock-backed** | Zepto and a second commerce provider (pricing), Pine Labs (payments), Delhivery (logistics), and Swiggy Instamart (cart building, over its MCP server). They compute deterministic answers. **No money moves and no order is placed.** Instamart has a live rail, but it needs an access token this deployment does not hold, and even live it can only build a cart — checkout is refused at the gate. |
| **Deliberately gated** | Pine Labs live mode raises `NotYetValidated` at construction; Gnani speech-to-**text** refuses without explicit acknowledgment. Both are blocked on open research questions. Text-to-speech is not gated and does run. |

The startup log says the same thing in its own words: *"Active mocks: zepto,
commerce_second_provider, pinelabs, delhivery, instamart — do not present these as live to
a judge."*

What this deployment does **not** do is in
[`docs/honest-limits.md`](docs/honest-limits.md): no local model behind the two
remote ones, an ephemeral database re-seeded every six hours, and a per-process
audio cache and synthesis cap.

## Status

- **264 tests passing, 3 skipped** (the 3 need live AWS credentials).
  **100% line and 99% branch coverage on `app/services.py`**, the deterministic
  decision core — one partial branch in `effective_freshness` is genuinely not
  exercised, and is named here rather than rounded up.
- **BUILD IT (local) is complete and runs end to end with zero network calls.**
  This is the demoable system.
- **SHIP IT (AWS) is code-complete and locally validated** — CDK synthesizes
  cleanly, every provider implemented — **but never live-deployed**, as no AWS
  account was available. See [`docs/aws-gate-evidence.md`](docs/aws-gate-evidence.md).
- **Two live rails are intentionally gated, not merely untested.** Pine Labs
  raises `NotYetValidated`; Gnani STT refuses without acknowledgment while TTS
  runs freely (the open question is about a *microphone*, so it gates the
  listening leg only). TTS is verified live via `scripts/probe_gnani_tts.py`.

## Running it locally

```bash
pip install -r requirements-dev.txt
cp .env.example .env          # optional; every setting has a safe default
python -m app.main            # http://127.0.0.1:8000  — API docs at /docs
python scripts/seed_demo_households.py    # three furnished demo households
python scripts/seed_demo_households.py --refresh   # restore them in place, ids kept
```

It boots fully offline with zero credentials. Without model API keys, recipe
generation needs a local Ollama; everything else works.

```bash
pytest                                       # 264 passed, 3 skipped
pytest --cov=app --cov-report=term-missing   # coverage breakdown
```

## Architecture in one paragraph

**A model may propose; only code may decide, and only a human may authorise
spend.** All spend, freshness and approval logic lives in pure functions in
[`app/services.py`](app/services.py) with no I/O — and a test fails the build if
that module ever imports a provider. Every tool call (ordering, paying, speaking,
delivery) passes through one gate in
[`app/core/registry.py`](app/core/registry.py) that evaluates a deterministic
predicate first; a model may *request* a tool but can never invoke one. Local
(SQLite/APScheduler/Ollama) and AWS (DynamoDB/EventBridge/Bedrock) are two
implementations of the same four protocols, chosen in exactly one file,
[`app/core/container.py`](app/core/container.py).

## Recipe generation and audio

`POST /api/v2/households/{id}/loops/{loop_id}/recipe` alternates Groq and Gemini,
fails over to the other on an operational error, and falls back to local Ollama
only if both fail (there is no Ollama leg in the cloud). The prompt and the
generated recipe are **never written to the database**. Recipes are
deterministically rejected if they exceed the time limit, have under 60% stocked
ingredients, or exceed remaining budget; one correction is allowed before `422`.
Leftovers are already-cooked dishes, so they go in a separate `leftovers_used`
list and never in `ingredients` — otherwise they would read as missing and get
priced. A recipe that reuses a leftover the household does not have, that has
expired, or beyond its portions is rejected the same way.

`POST /api/households/{id}/loops/{loop_id}/instamart-cart` turns a loop's
missing ingredients into a Swiggy Instamart cart: it searches each one, takes the
first **in-stock** variant (an out-of-stock one is reported as unmatched, never
added), replaces the cart, and reads it back. Only cart methods are on the
gate's allowlist in [`app/core/registry.py`](app/core/registry.py); `checkout`
and the payment tools are refused by name.

A successful response carries an opaque `audio_id`. **Nothing is rewritten or
synthesized until it is fetched**, so a cook who never presses play costs nothing
in tokens or vendor credits. The briefing is Hinglish by default — code-mixed
Hindi and English in Latin script, the way an Indian kitchen actually sounds.
Quantities are spelled out in words, digits and currency symbols are rejected
deterministically before reaching the speech engine, and **prices, spend tiers
and approval state are never spoken**.

## Deploying it

The `Dockerfile` is host-agnostic; `render.yaml` deploys it to Render's free
tier. Nothing pins a port — `Settings.resolved_port()` reads `$PORT`. Four
secrets are set in the host's dashboard, never in a tracked file:
`HOUSEHOLD_API_KEY`, `HOUSEHOLD_GROQ_API_KEY`, `HOUSEHOLD_GEMINI_API_KEY`,
`HOUSEHOLD_GNANI_API_KEY`. **With no API key configured the container refuses to
start.**

## Further reading

| | |
|---|---|
| [`JUDGES.md`](JUDGES.md) | The guided tour — start here |
| [`docs/honest-limits.md`](docs/honest-limits.md) | What does not work, and why |
| [`docs/ticket-map.md`](docs/ticket-map.md) | Where each of the 46 build-map tickets landed |
| [`docs/aws-gate-evidence.md`](docs/aws-gate-evidence.md) | What the AWS track does and does not prove |
| [`docs/demo-script.md`](docs/demo-script.md) | A run-sheet for the demo |
| [`docs/submission-alignment.md`](docs/submission-alignment.md) | How this maps to the brief |
| [`CLAUDE.md`](CLAUDE.md) | Architectural rules, for anyone extending it |

**If you only check three things:** the approval gate failing closed
(`test_stale_approval_refused_when_basket_changed`), the loop that cannot
silently succeed (`test_backdated_unconfirmed_loop_is_flagged_unclosed`), and
the import-boundary test that keeps providers out of the decision core.
