"""Shared error types for recipe-generation model providers.

These failures are intentionally separate from recipe constraint failures.
An unavailable provider may be failed over; a valid recipe that is too slow
or too expensive must be corrected against the same household context.
"""
from __future__ import annotations


class RecipeProviderOperationalError(RuntimeError):
    """A retryable provider/configuration/output failure.

    `retry_after_seconds` is populated for quota/rate-limit responses so the
    provider chain can temporarily avoid a known-unavailable remote service.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)
