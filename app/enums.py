"""Typed state vocabulary — Ticket #2.

Every tier and status in the system used to be a free `str` field. That meant
a typo like ``"awaiting_aproval"`` would silently write a corrupted record
and the route would still return 200. These enums are the single source of
truth for every value that used to be a string literal scattered across
models.py, schemas.py, scheduler.py and seed.py.

Values below cover every status string already present in seed.py's demo
scenarios (recorded, triggered, planned, awaiting_approval, cooking,
completed) plus "unclosed" for Ticket #24's timeout sweep.
"""

from __future__ import annotations

from enum import Enum


class SpendTier(str, Enum):
    """Ticket #11's classifier output. Fail-closed: an unrecognized or
    unclassifiable situation must resolve to RED, never GREEN."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class LoopStatus(str, Enum):
    """MealLoopRecord.status. UNCLOSED is not a failure state the loop can
    be posted into directly — it is written only by Ticket #24's sweep."""

    RECORDED = "recorded"
    TRIGGERED = "triggered"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    COOKING = "cooking"
    COMPLETED = "completed"
    UNCLOSED = "unclosed"

    @classmethod
    def terminal(cls) -> set["LoopStatus"]:
        """States after which the loop is considered closed and the sweep
        in Ticket #24 should no longer touch it."""
        return {cls.COMPLETED, cls.UNCLOSED}


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProcurementPath(str, Enum):
    """Ticket #21's consolidator output — which path was chosen to close
    an ingredient gap."""

    ALREADY_STOCKED = "already_stocked"
    TOP_UP_ORDER = "top_up_order"
    MANUAL_PURCHASE = "manual_purchase"
    ESCALATED = "escalated"  # no affordable/feasible path — red-tier approval


class FreshnessState(str, Enum):
    """Ticket #9's reconciled freshness authority. Distinct from the raw
    InventoryLot.freshness field, which is only the visual-capture input."""

    FRESH = "fresh"
    EXPIRES_TODAY = "expires_today"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    USE_IMMEDIATELY = "use_immediately"
    STALE = "stale"  # not updated within the recency window
    UNKNOWN = "unknown"

    @property
    def usable(self) -> bool:
        """Whether a lot in this state counts toward stock for Ticket #10's
        gap calculator. Conservative by design — STALE and UNKNOWN do not
        count, matching Ticket #9's "fail safe toward asking the human"."""
        return self in {FreshnessState.FRESH, FreshnessState.EXPIRES_TODAY, FreshnessState.EXPIRING_SOON}


class Language(str, Enum):
    """Gnani Timbre v2.5 language codes — the typed vocabulary for speech.

    CookProfile.language, HouseholdMember.language and
    Household.default_language are all free `str` columns holding a human
    name ("Hindi", "English"). Gnani needs a fixed code. This enum is the
    only place those codes are written down, for the same reason the rest
    of this module exists: a mistyped "hi-In" must fail loudly, not send a
    request that comes back voiced in the wrong language.

    The profile-name to (code, voice) resolution lives in
    Settings.gnani_voice_map so an operator can retune voices without a
    code change. `script` lives here instead, because which script a
    language is written in is a fact about the language, not an operator
    preference — and the rewrite prompt and the TTS call must never
    disagree about it.

    Gnani's "auto" is deliberately absent: it asks the vendor to guess the
    language, and an audio briefing confidently delivered in the wrong one
    is worse than no audio at all.
    """

    HI_IN = "hi-IN"
    EN_IN = "en-IN"
    HI_EN = "hi-en"  # Hinglish — code-mixed, written in Latin script
    KN_IN = "kn-IN"
    TA_IN = "ta-IN"
    TE_IN = "te-IN"
    ML_IN = "ml-IN"
    MR_IN = "mr-IN"
    PA_IN = "pa-IN"
    BN_IN = "bn-IN"
    GU_IN = "gu-IN"

    @property
    def script(self) -> str:
        """The script the rewrite step must produce for this language.

        Latin for English and for Hinglish (which is *defined* by being
        typed in Latin); the language's own script otherwise. Sending
        Latin-transliterated Hindi to the hi-IN voice is the mispronunciation
        case this property exists to prevent.
        """
        return _SCRIPT_BY_LANGUAGE[self]

    @property
    def display_name(self) -> str:
        """How to name this language to a language model.

        The enum's *value* is a routing code for Gnani. Putting "hi-en" into
        a natural-language prompt asks a model to write in a string it has
        no strong prior for; Hinglish especially degrades into either pure
        English or transliterated-everything. This is the human name that
        goes in the prompt instead.
        """
        return _DISPLAY_NAME_BY_LANGUAGE[self]


_SCRIPT_BY_LANGUAGE: dict[Language, str] = {
    Language.HI_IN: "Devanagari",
    Language.EN_IN: "Latin",
    Language.HI_EN: "Latin",
    Language.KN_IN: "Kannada",
    Language.TA_IN: "Tamil",
    Language.TE_IN: "Telugu",
    Language.ML_IN: "Malayalam",
    Language.MR_IN: "Devanagari",
    Language.PA_IN: "Gurmukhi",
    Language.BN_IN: "Bengali",
    Language.GU_IN: "Gujarati",
}

_DISPLAY_NAME_BY_LANGUAGE: dict[Language, str] = {
    Language.HI_IN: "Hindi",
    Language.EN_IN: "Indian English",
    # Spelled out at length on purpose. "Hinglish" alone gets read as either
    # "English with a few Hindi words" or "Hindi with everything
    # transliterated", and neither is how a kitchen actually sounds.
    # No "written in Latin script" here: the prompt appends the script clause
    # right after the name, and saying it twice reads as an error.
    Language.HI_EN: "Hinglish — everyday spoken Hindi mixed with English, the way Indian households actually talk",
    Language.KN_IN: "Kannada",
    Language.TA_IN: "Tamil",
    Language.TE_IN: "Telugu",
    Language.ML_IN: "Malayalam",
    Language.MR_IN: "Marathi",
    Language.PA_IN: "Punjabi",
    Language.BN_IN: "Bengali",
    Language.GU_IN: "Gujarati",
}
