"""Ephemeral store for generated recipes awaiting an audio briefing.

The v2 recipe endpoint never writes a generated recipe or its prompt to the
database, and that invariant holds here: an MP3 of a recipe read aloud *is*
the recipe in a lossier container, so persisting one would break the rule in
substance while leaving its letter intact. Everything below lives in
process memory, bounded by both a TTL and an entry count, and is gone on
restart.

Why a cache exists at all: synthesis happens on demand, at fetch time, not
when the recipe is generated. A cook who never presses play costs nothing
in model tokens or Gnani credits. That means the recipe has to survive
between the POST that produced it and the GET that voices it, and this is
where it waits.

Known limitation, shared with the daily synthesis cap in
app/core/registry.py: this is per-process. Under more than one uvicorn
worker a GET can land on a process that never saw the recipe, and the cap
counts per worker. Both want the same shared store (Redis or equivalent)
and should move together when this is deployed multi-worker -- see
docs/honest-limits.md. Single-worker local operation is unaffected.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

UTC = timezone.utc


@dataclass
class RecipeAudioEntry:
    """One generated recipe, plus whatever has since been derived from it."""

    household_id: int
    loop_id: int
    recipe: Any                      # app.schemas.GeneratedRecipe
    missing_ingredients: list[dict[str, Any]]
    # Resolved once, when the recipe is generated, and carried rather than
    # re-derived at fetch time. Re-resolving on the GET meant a settings
    # change between the two requests could hand the synthesis call a
    # language with no voice -- the POST checked for that and the GET did not.
    language: Any                    # app.enums.Language
    voice: str
    skill_level: str
    created_at: datetime
    # Filled in lazily, on the first GET that asks for audio.
    spoken_text: str | None = None
    audio: bytes | None = None
    media_type: str | None = None
    # Held by the audio route while it rewrites and synthesizes, so two
    # near-simultaneous plays of the same recipe do one round of work
    # instead of two.
    lock: Lock = field(default_factory=Lock, repr=False)


class RecipeAudioCache:
    """Opaque-id keyed, TTL- and size-bounded, household-scoped on read."""

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, RecipeAudioEntry] = {}
        self._lock = Lock()

    def put(self, entry: RecipeAudioEntry) -> str:
        """Store an entry and return its opaque id.

        The id is unguessable *and* the read path re-checks the household
        and loop, so a leaked id is not a cross-household read path. A fresh
        id per generation also means a second recipe for the same loop can
        never be served the first one's audio.
        """
        audio_id = secrets.token_urlsafe(16)
        with self._lock:
            self._evict(datetime.now(UTC))
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=lambda key: self._entries[key].created_at)
                del self._entries[oldest]
            self._entries[audio_id] = entry
        return audio_id

    def get(self, audio_id: str, household_id: int, loop_id: int) -> RecipeAudioEntry | None:
        """Return the entry only when the caller's household and loop match.

        Every other read path in this system is household-scoped through
        app/repositories.py. A cache is not a repository, so the scoping is
        re-asserted here explicitly rather than relying on the URL alone.
        """
        now = datetime.now(UTC)
        with self._lock:
            self._evict(now)
            entry = self._entries.get(audio_id)
        if entry is None:
            return None
        if entry.household_id != household_id or entry.loop_id != loop_id:
            return None
        return entry

    def _evict(self, now: datetime) -> None:
        """Caller must hold the lock."""
        cutoff = now - timedelta(seconds=self.ttl_seconds)
        for key in [key for key, entry in self._entries.items() if entry.created_at < cutoff]:
            del self._entries[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
