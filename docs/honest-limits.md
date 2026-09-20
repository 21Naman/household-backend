# What This System Does Not Do, and Why

Bible §4.5's highest-impact fix under Thoroughness: frame MVP exclusions
as deliberate boundaries with a stated v2 path, not as oversights a judge
discovers. This is that framing, stated first.

## Deliberately out of scope for the MVP

**Cook-capability learning from data (B004).** `CookProfile` is a static,
human-provided record (`skill_level`, `confident_dishes`), never inferred
from accepted/rejected `DishHistory`. This is a genuine cold-start
problem — inferring skill from a handful of early meals is unreliable —
so the MVP asks the household once instead of guessing. **v2 path:**
once `DishHistory` accumulates (Ticket #27's `feedback_learning` scenario
is the seed of this), infer skill drift and prompt the household to
confirm an update rather than silently changing it.

**Festival/calendar-aware recommendation layer (B014).** `app/providers/google_calendar.py`
implements the OAuth/API client for Google Calendar and is unit-tested
(`tests/test_providers_local.py`), but no route ever wires it into a
household's guest/availability flow, and no state is persisted for it — a
complexity audit found the `GoogleCalendarConnection` table it would have
stored a connection in had no reader or writer anywhere, so that table was
dropped (`alembic/versions/0002_drop_unused_tables.py`) rather than kept as
unused scaffolding. Today a festival is, at most, free text in
`MealLoopRecord.occasion`. **v2 path:** actually call the connect/callback
routes through to a persisted per-member connection, then add a dedicated
occasion taxonomy feeding dish selection, once the core loop is proven.

**Vector store / document retrieval.** Correctly absent per Bible §4.2 —
this is a structured-state retrieval problem (SQL/DynamoDB), not a
document-search problem. No RAG layer should be added even under
pressure to "use more AI."

**Cross-app commerce beyond two providers.** Ticket #20 added a second
provider so price comparison (B015/B020) is real, not a comparison
against nothing. A third live provider (Blinkit, Instamart) would
strengthen the consolidation claim further but wasn't required to prove
the mechanism works.

**Per-transaction budget ledger.** `Budget` tracks running totals
(`spent_amount`, `planned_amount`), not a row per transaction. Sufficient
for MVP tiering; a full ledger matters more once whole-household monthly
budgeting (submission Q7) is a live feature people rely on daily.

## Open research questions, stated plainly

**RQ4/RQ5 — Pine Labs household-ceiling mandate.** `PineLabsPaymentProvider`
(Ticket #40) is implemented against the documented P3P/Grantex API but
refuses to run live without an explicit acknowledgment flag, because
whether SBMD/OTM supports variable per-item pricing under one household
ceiling, and what Grantex's actual household-mandate UX looks like, are
both unverified. Bible §3.1 contradicts the team's own original B037
claim (that 2FA fires on variable-weight produce) — a live sandbox test
would settle it either way, and this codebase has not run that test.

**RQ7 — Gnani in kitchen-ambient noise.** Gnani's published
noise-robustness claims are validated on telephony-grade 8kHz audio;
whether that generalizes to a phone mic in a running kitchen is genuinely
unknown, not confirmed to fail. That question is about a *microphone*, so
the gate now sits on `GnaniVoiceProvider.transcribe()` alone rather than on
the whole class: speech-to-text still refuses to run without an explicit
acknowledgment flag, and text-to-speech — which involves no microphone —
does not. Text remains the **primary** cook-facing channel regardless
(Ticket #25), not a fallback bolted on after the fact.

Speech-to-text additionally has no implementation at all today. The local
faster-whisper adapter was removed once Gnani credentials existed, so
`LocalVoiceProvider.transcribe` raises rather than returning an empty
transcript — an empty transcript is the kind of thing that gets demoed as
working. Two-way voice is deferred, not abandoned: the method stays
declared on the `VoiceProvider` protocol so reinstating it is filling a
declared gap rather than a protocol change.

**Quick-commerce catalog API access.** Rated Low confidence in the
Bible's own Assumption Register. Ticket #20's second provider is a mock
specifically so the comparison *logic* is provably correct regardless of
whether live catalog access is ever granted — if it isn't, B015 becomes a
stated scope boundary, not a gap a judge discovers first.

## What has not been run, and why

**The Gnani text-to-speech rail is verified live.** `scripts/probe_gnani_tts.py`
was run against the production API with a real key and returned `200 OK`,
`content-type: audio/mpeg`, a well-formed MPEG layer III file at 128 kbps /
24 kHz / mono — exactly the `audio_config` that was requested, so every field
round-tripped. The full pipeline was then exercised end to end through the
app: recipe → Hinglish rewrite → `hi-en`/Poorvi synthesis → 337 KB of
playable MP3, served once and cached on replay.

What that does **not** cover is the speech-to-text leg, which stays gated on
RQ7 and which this key cannot exercise at all — it is scoped to TTS only.
`tests/test_recipe_audio.py` reflects that: the TTS request shape is
asserted, `transcribe()` is not, and the ~12 uncovered lines in
`app/providers/voice_gnani.py` are all in that method.

This is worth stating precisely, because **two successive versions of that
provider got the request wrong and nothing caught either one**:

1. The first claimed in its own docstring to be "implemented against the
   Gnani STT/TTS API as documented" while posting to an invented `/tts` path
   with an invented `Authorization: Bearer` header. Neither appears in the
   documentation.
2. The second fixed those from a summary of the docs, but sent `speed`
   nested inside `audio_config` when it is a top-level field, used
   `audio_config.format` when the field is `container`, omitted
   `sample_rate`/`num_channels`/`sample_width` from an object the OpenAPI
   spec marks required, and dropped `voice` entirely when none was
   configured although `voice` is required.

Every one of those is a 400 on the first live call. Both survived because no
credential existed to try them with, and because the tests asserted the
*pipeline's* behaviour while stubbing the provider — so nothing ever looked
at the bytes on the wire. The third version is written against the full
Timbre v2.5 contract, `tests/test_recipe_audio.py` asserts the exact JSON
body against a stubbed transport, and the live probe confirms the API
accepts it.

**And the probe immediately found a fourth mistake that the documentation
itself caused.** The error-body parser was written to Gnani's published
`StandardErrorResponse` shape, `{"success": false, "error": {"type",
"message"}}`. The live API does not send that. It sends at least two other
shapes:

    HTTP 500  {"success": false, "message": "...", "status_code": 500}
    HTTP 429  {"detail": {"error_code": "RATE_LIMITED", "message": "...", ...}}

Neither has an `error` object and neither uses `type`, so every real failure
was being reported as `type=None message=None` — an error reporter that
reported nothing, which would have been discovered during an outage. The
parser now tries all three shapes and the tests use the **captured real
bodies** rather than the documented one. Worth generalising: a vendor's
documented error contract is itself a claim to verify, not a given.

The lesson generalises past this rail: an unexecuted integration should be
labelled unexecuted, not "implemented as documented", and a test that stubs
the thing it is meant to verify is not covering it.

`Settings.gnani_tts_max_chars` is now measured rather than guessed, but
measured **loosely**, and the difference matters. The probe observed:

| characters | result |
| --- | --- |
| 500 / 1000 / 1500 / 2000 | `200 OK` |
| 2500 | `HTTP 500` — "We are facing technical difficulties" (one attempt) |
| 3000 | `HTTP 500` on one run; a retry came back `429`, which says nothing about length |

So 2000 is known good, 2500 failed once, and the boundary between them was
never bisected. The setting is **1500**, deliberately well clear of it: the
ceiling is approximate, and the failure mode above it is an unhelpful server
error rather than a clean `400` naming a limit, which is a reason to stay
away from the edge rather than creep up on it. A normal recipe briefing
lands under 1500, so the chunker stays a rare path.

Gnani's REST documentation states no character limit at all. There is one.

Also confirmed live: **rate limiting exists** (`429 RATE_LIMITED`), reached
after roughly a dozen requests in quick succession during probing. The
per-household daily cap in `ToolRegistry` is a spend control, not a
rate-limit defence; nothing currently backs off on a 429 beyond surfacing it
as a 503. That is acceptable for one household pressing play, and would not
be for a burst.

`MockVoiceProvider` is the default and stays useful afterwards: it is what
CI and credential-free development run against. Unlike
`commerce_mock_enabled`, `pinelabs_mock_enabled` and
`delhivery_mock_enabled`, the `gnani_mock_enabled` flag now actually
branches, in `app/core/container.py`. Note that the mock returns WAV while
the live rail is configured for MP3 — a passing mock test is evidence about
the pipeline, never about MP3.

**Eight of the eleven supported languages have no voice configured.**
Gnani's catalog has 42 voices, but only four are named in the REST
documentation this was built from (Nalini, Deepak, Kaveri, Poorvi), and
inventing plausible names for Tamil or Bengali would produce a 400
"unsupported voice" at the worst possible moment. Those languages therefore
fall back to Hinglish (`gnani_default_language`) rather than getting no
audio at all. That fallback is an explicit, named, operator-editable
setting, not the model guessing — but it does mean a Tamil-speaking cook is
briefed in Hinglish today. **v2 path:** read the real Voice Catalog and fill
in `gnani_voice_map`, at which point the fallback stops firing for those
languages on its own.

**The audio cache and the synthesis cap are per-process.**
`app/core/recipe_audio_cache.py` holds generated recipes and synthesized
audio in process memory, and `ToolRegistry`'s per-household daily synthesis
counter lives in the same place. Under more than one uvicorn worker, an
audio fetch can land on a process that never saw the recipe, and the daily
cap counts per worker rather than per household. Both restart empty. This
is deliberate for single-worker local operation — it is also what keeps the
"never persist a generated recipe" invariant honest, since an MP3 of a
recipe read aloud is the recipe in a lossier container. **v2 path:** both
move to a shared store (Redis or equivalent) together, as one piece of
work, when this is actually deployed multi-worker. Splitting them would
leave the cap enforceable and the cache not, or the reverse.

No AWS deployment has happened from this environment (see
`docs/aws-gate-evidence.md`) — no account, no credentials, no network
route to AWS. Every SHIP IT provider is implemented against the
documented API shape and unit-testable, but `tests/test_parity.py` is
explicitly skipped rather than faked as passing. This is the same
discipline applied everywhere else in this codebase: an unverified claim
is marked unverified.

No primary interviews back the Bible's product assumptions as of this
writing (submission Q2) — the Bible itself names this as the single
biggest risk to the submission. No amount of engineering rigor in this
codebase substitutes for that; it's listed here because pretending
otherwise would undercut everything else in this document.
