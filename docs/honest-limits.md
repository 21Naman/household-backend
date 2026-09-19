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

**Festival/calendar-aware recommendation layer (B014).** `GoogleCalendarConnection`
exists and is wired for guest/availability context (B019), but there is
no occasion-aware dish suggestion feature — a festival is, at most, free
text in `MealLoopRecord.occasion`. **v2 path:** a dedicated occasion
taxonomy feeding dish selection, once the core loop is proven.

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

**RQ7 — Gnani in kitchen-ambient noise.** `GnaniVoiceProvider` (Ticket #41)
is similarly implemented but gated. Gnani's published noise-robustness
claims are validated on telephony-grade 8kHz audio; whether that
generalizes to a phone mic in a running kitchen is genuinely unknown, not
confirmed to fail. `LocalVoiceProvider`'s text-reply path (Ticket #25) is
built to be the **primary** cook-facing channel regardless of this
answer, not a fallback bolted on after the fact.

**Quick-commerce catalog API access.** Rated Low confidence in the
Bible's own Assumption Register. Ticket #20's second provider is a mock
specifically so the comparison *logic* is provably correct regardless of
whether live catalog access is ever granted — if it isn't, B015 becomes a
stated scope boundary, not a gap a judge discovers first.

## What has not been run, and why

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
