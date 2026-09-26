# Aaj Kya Banega — A Guide for Judges

A household orchestration agent that answers the question every Indian kitchen
asks at 5pm: *what are we cooking tonight?* It plans a meal against what is
actually in the kitchen, works out what is missing, decides whether that gap is
worth ordering, asks a human before spending real money, and closes the loop
when someone confirms they cooked it.

Built in a 24-hour hackathon. This document is the honest account of what got
built, what was deliberately left out, and what we thought about along the way.

**Live demo:** https://aaj-kya-banega-zhml.onrender.com
**API key:** `FV0PIhC5gKLushnUtnq1xcQ6e6lg0eHd` — paste it into the field at the
top of the page. Your browser remembers it.

> The first request may take ~25 seconds while the free-tier container wakes up.
> Everything after that is instant. It is not broken.

---

## 1. Two minutes, if that is all you have

1. Open the URL, paste the API key.
2. Pick **Iyer Household** from the dropdown.
3. Look at the **left pane**. That is everything the model is told about this
   family — and, separately labelled, the budget and kitchen the model is *not*
   told. One preference signal is struck through and marked expired: the system
   knows about it and deliberately does not act on it.
4. Press **Generate**. A recipe comes back from a live LLM, then gets priced and
   tiered by code the model never touches.
5. Press **Read it to me**. That is a real voice from a real text-to-speech
   vendor, speaking Hinglish.
6. Press **I cooked this**. Watch the kitchen inventory decrement in the left pane.

The three households differ on purpose:

| Household | What it is there to show |
|---|---|
| **Sharma** | Well stocked, room in the budget — the unremarkable happy path |
| **Iyer** | ₹350 left in the month, and an English-speaking cook in a Tamil household — spend pressure and the voice-language fallback |
| **Khan** | A beginner cook and guests coming — how register and occasion change the output |

---

## 2. The one idea the whole thing is built around

**A language model may propose. It may never decide, and it may never spend.**

Everything in this codebase follows from that. Concretely:

- The model proposes a recipe. **Code** decides whether it is affordable, fast
  enough, and sufficiently stocked — and rejects it if not.
- The model never returns a price, a spend tier, or approval authority. Those
  come from pure functions in [`app/services.py`](app/services.py) that take no
  network and do no I/O.
- The model can *request* a tool call. It cannot *make* one. Every tool
  invocation — ordering, paying, speaking, checking delivery — passes through a
  single gate in [`app/core/registry.py`](app/core/registry.py) that evaluates a
  deterministic predicate before the provider is touched.

This is mechanically enforced, not just intended. A test in
[`tests/test_services.py`](tests/test_services.py) fails the build if
`app/services.py` ever imports from `app/providers/` — because the day that
import appears is the day the decision core can be influenced by a vendor
response.

---

## 3. What actually runs — no hedging

Judges routinely misjudge hackathon projects in one of two directions: assuming
everything is smoke and mirrors, or assuming a demo means production. Here is
the precise split.

### Live external calls, spending real credits

| | |
|---|---|
| **Groq** (`openai/gpt-oss-120b`) | Recipe generation and the spoken-briefing rewrite. Verified working on the deployed instance. |
| **Gemini** (`gemini-3.8-flash`) | Same role, alternating with Groq. Works, but the free tier rate-limits aggressively, so Groq carries most traffic in practice. |
| **Gnani Timbre v2.5** | Text-to-speech. A real vendor API returning real MP3s. The deployed instance serves ~1.6 MB of genuine 24 kHz audio per briefing. |

### Mock-backed — deterministic, no money moves

Zepto and a second commerce provider (pricing), Pine Labs (payments),
Delhivery (logistics), Google Calendar. These compute real answers from real
logic; they just don't call the vendor. **No order is placed and no payment is
made anywhere in this system.**

### Deliberately gated — refuses to run, on purpose

Two rails raise an exception rather than execute, because the build map marks
them blocked on unresolved research questions:

- **Pine Labs live mode** raises `NotYetValidated` at construction.
- **Gnani speech-to-text** raises `GnaniNotYetValidated` — but text-to-speech
  runs freely. The open question is whether Gnani's noise robustness survives a
  noisy kitchen via a phone mic. That is a question about a *microphone*, so it
  gates the listening leg and not the speaking leg. (The API key we hold is
  TTS-scoped anyway, so STT could not run even if we lifted the gate.)

This distinction matters: "we didn't get to it" and "we decided it isn't safe to
turn on yet" are different engineering positions, and the second one is written
into the code rather than into a slide.

### Code-complete but not live

The AWS track (Bedrock, DynamoDB, EventBridge, Lambda, CDK) is fully
implemented and the CDK synthesizes cleanly, but it has never been deployed —
no AWS account was available. [`docs/aws-gate-evidence.md`](docs/aws-gate-evidence.md)
states exactly what that does and does not prove.

---

## 4. Architecture: one core, two tracks

The central design rule is that **BUILD IT** (local: Ollama, SQLite,
APScheduler) and **SHIP IT** (AWS: Bedrock, DynamoDB, EventBridge, Lambda) are
two implementations of the same four protocols in
[`app/core/interfaces.py`](app/core/interfaces.py): `ModelProvider`,
`StateStore`, `EventScheduler`, and the tool-provider protocols.

Routes and services never import a concrete provider. Everything is resolved
once at startup in [`app/core/container.py`](app/core/container.py) — that file
is the *only* place in the codebase where "local or AWS?" is answered.

The payoff: the same request handler, the same decision logic and the same tests
run against either track. The demo you are clicking is the local track. Nothing
about it is a special demo path.

```
HTTP route  →  deterministic services  →  gated tool registry  →  provider
                (no I/O, no network)       (predicate first)      (local or AWS)
                        ↑
                  model proposes here, and only here
```

---

## 5. The three behaviours worth your attention

### 5.1 The spend gate fails closed, and re-checks at execution time

Approving a purchase is not a blank cheque. The approval records the *amount*
that was approved. At execution time the system compares it against the basket
in hand — and if the basket changed, it refuses and names both figures.

You can trigger this in the demo: generate a recipe that needs approval, approve
it, re-generate so the basket changes, then execute. You get a **403** rather
than a purchase.

An unrecognised or stale state always resolves to *requires re-approval*, never
to *proceed*. That direction is the entire point.

### 5.2 `execute-order` takes nothing from the caller

This one is a fix we made *during* the build after reviewing our own code, and
it is the change we are most glad about.

The endpoint originally accepted `amount_inr` and `tier` as query parameters.
Which meant the gate was validating numbers the *caller* had chosen — passing
`?tier=green` made the approval check return early without ever looking for an
approval record. On a local demo that is invisible. On a public URL it is
bypassable from browser devtools.

Now the endpoint accepts a household and a loop, and nothing else. The amount
and tier are read from a basket the **server** priced and recorded. A loop with
no recorded basket is refused with a 409 rather than defaulted to zero.

### 5.3 The loop can never silently claim success

A background sweep flags meal loops that were started but never confirmed,
after a configurable timeout. Without it, a plan that nobody ever cooked sits in
the database looking exactly like a success. "We don't know what happened" is a
real state and the system can represent it.

---

## 6. Things we thought about that are easy to miss

**The freshness rule is a food-safety rule, not a config knob.** Inventory older
than a recency window reads as STALE and is excluded from planning. On the
deployed instance this caused a problem: seeded demo data ages past the window
and the kitchen goes empty, so recipe generation starts failing for no visible
reason. The tempting fix was to widen the window on the deployed instance. We
rejected that and fixed the *data* instead — a job restores the demo kitchen in
place every six hours. Weakening a safety rule to avoid an error screen is the
kind of shortcut that is invisible in a demo and dangerous in production.

**Nothing about money is ever spoken aloud.** The audio briefing tells the cook
what is missing from the kitchen, because that changes what they do. It never
mentions cost, spend tier, or whether anyone approved anything. Spend authority
is a human decision on a different channel, and a voice in a kitchen is the
wrong channel for it. This is enforced by a verifier, not by prompt politeness.

**No digits survive into spoken text.** "₹250" and "2" mispronounce badly in
Hindi TTS, and so do Devanagari digits. A deterministic check rejects them; the
model gets one chance to correct, and if it fails the response degrades to text
rather than synthesizing something that will mispronounce.

**Recipes are never persisted.** Not the prompt, not the generated recipe. The
audio is cached in process memory with a TTL, never in the database — an MP3 of
a recipe read aloud is the recipe in a lossier container, so storing one would
break the invariant in substance while keeping it on paper.

**Synthesis happens when you press play, not when the recipe is generated.** A
cook who never presses play costs nothing in model tokens or vendor credits.

**The language a recipe is spoken in comes from the cook, not the household.**
The cook is the person listening. Iyer Household is Tamil-speaking; their cook
Murugan speaks English. Only four voices are configured, so most languages fall
back to Hinglish — and that fallback is a named setting, not the model guessing.

**Duplication that is deliberate.** A member's health constraint lives both as a
structured field *and* as a narrative preference signal. The field is the fact;
the signal is the story with provenance that the model actually reads. We kept
both on purpose.

**Remaining budget has exactly one definition.** Four copies of
`monthly_limit - spent - planned` had accumulated across the codebase, which
meant the model could be shown a different remaining budget than the gate
enforced. There is now one function, and every caller uses it.

---

## 7. What it does not do

Summarised from [`docs/honest-limits.md`](docs/honest-limits.md), which is
longer and more specific:

- **No money moves.** Payments are mock-backed end to end.
- **The deployed database is ephemeral.** Every container restart is a fresh
  database, seeded at startup. Loop ids and audit rows do not survive a restart.
- **The deployed instance has two model legs, not three.** There is no Ollama in
  the cloud, so if both Groq and Gemini fail, recipe generation returns 503 with
  no local backstop. The three-leg chain is a property of the local build.
- **The audio cache, the vendor-credit counter and the scheduler are all
  per-process.** The free tier stops the container after ~15 minutes idle, which
  empties all three. An `audio_id` minted before an idle gap returns 404 after
  it. The daily synthesis cap is real with one worker and multiplies with more.
- **The six-hour unclosed-loop timeout will effectively never elapse on the free
  tier**, because the process does not stay alive that long. The behaviour is
  real and tested; it is just not observable on this deployment.
- **The API key is a demo credential**, published so you can use the demo. It is
  not a production posture.
- **Single-user, single-tenant.** No accounts, no roles, no multi-worker story.

---

## 8. Verifying it yourself

```bash
git clone https://github.com/21Naman/household-backend
cd household-backend
pip install -r requirements-dev.txt
python -m app.main                                # http://127.0.0.1:8000
```

It boots fully offline with zero credentials — every setting has a safe default
and every remote rail is mock-backed unless you configure it otherwise. Without
model API keys, recipe generation needs a local Ollama; everything else works.

```bash
pytest                                            # 264 passed, 3 skipped
pytest --cov=app.services --cov-branch            # 99% branch coverage on the decision core
```

The 3 skipped tests require live AWS credentials. The decision core sits at 99%
branch coverage with one partial branch in the freshness check — we know which
one, and we would rather say so than round it up.

**If you check three things, check these:**

1. `test_stale_approval_refused_when_basket_changed` in
   [`tests/test_services.py`](tests/test_services.py) — the approval gate failing
   closed.
2. `test_backdated_unconfirmed_loop_is_flagged_unclosed` in
   [`tests/test_unclosed_sweep.py`](tests/test_unclosed_sweep.py) — the loop that
   cannot silently succeed.
3. The import-boundary test in [`tests/test_services.py`](tests/test_services.py)
   — the wall between the decision core and the providers, enforced by CI rather
   than by discipline.

---

## 9. Where everything is

| Looking for | Go to |
|---|---|
| Full ticket-by-ticket map of the build | [`README.md`](README.md) |
| What does not work, and why | [`docs/honest-limits.md`](docs/honest-limits.md) |
| What the AWS track does and does not prove | [`docs/aws-gate-evidence.md`](docs/aws-gate-evidence.md) |
| A run-sheet for the demo | [`docs/demo-script.md`](docs/demo-script.md) |
| How the submission maps to the brief | [`docs/submission-alignment.md`](docs/submission-alignment.md) |
| Architectural rules, for anyone extending it | [`CLAUDE.md`](CLAUDE.md) |
| Interactive API reference | `/docs` on the live URL |

---

Thank you for reading this far. The parts of this project we are proudest of are
the refusals — the purchase that doesn't go through, the tool call that doesn't
fire, the rail that won't turn on. They are harder to demo than features, which
is exactly why they are written down here.
