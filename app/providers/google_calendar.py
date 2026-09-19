"""Small OAuth and Calendar API client kept separate from household policy.

Serves guest/availability context (Bible B019) ahead of a dedicated feature
-- it is not one of the Bible's three named rails (Gnani/Delhivery/Pine
Labs). Ticket #38 narrowed the requested scope from
`calendar.events.owned` (present in Google's client-library scope
constants and real integrations, but absent from Google's main Calendar
API scopes reference page) to the documented `calendar.freebusy` +
`calendar.events.readonly` pair, which is all this provider actually uses:
free/busy checking and event-label reads, never event creation.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
import httpx

from app.settings import Settings


class GoogleCalendarError(RuntimeError):
    pass


class GoogleCalendarProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _credentials(self) -> tuple[str, str, Fernet]:
        if not (self.settings.google_client_id and self.settings.google_client_secret and self.settings.google_token_encryption_key):
            raise GoogleCalendarError("Google Calendar is not configured. Add Google OAuth credentials and a token encryption key to .env.")
        try:
            return self.settings.google_client_id, self.settings.google_client_secret, Fernet(self.settings.google_token_encryption_key.encode())
        except (ValueError, TypeError) as exc:
            raise GoogleCalendarError("Google token encryption key is invalid.") from exc

    def authorization_url(self, state: str) -> str:
        client_id, _, _ = self._credentials()
        query = urlencode({
            "client_id": client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": self.settings.google_calendar_scopes,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        })
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    def exchange_code(self, code: str) -> tuple[str, str, str]:
        client_id, client_secret, cipher = self._credentials()
        try:
            response = httpx.post("https://oauth2.googleapis.com/token", data={
                "code": code, "client_id": client_id, "client_secret": client_secret,
                "redirect_uri": self.settings.google_redirect_uri, "grant_type": "authorization_code",
            }, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            token = response.json()
            refresh_token = token["refresh_token"]
            access_token = token["access_token"]
            identity = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {access_token}"}, timeout=self.settings.request_timeout_seconds)
            identity.raise_for_status()
            profile = identity.json()
            return str(profile["sub"]), str(profile["email"]), cipher.encrypt(refresh_token.encode()).decode()
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise GoogleCalendarError("Google did not complete the Calendar connection. Please try again.") from exc

    def _access_token(self, encrypted_refresh_token: str) -> str:
        _, _, cipher = self._credentials()
        try:
            refresh_token = cipher.decrypt(encrypted_refresh_token.encode()).decode()
            token_response = httpx.post("https://oauth2.googleapis.com/token", data={
                "client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token",
            }, timeout=self.settings.request_timeout_seconds)
            token_response.raise_for_status()
            return str(token_response.json()["access_token"])
        except (InvalidToken, httpx.HTTPError, KeyError, TypeError) as exc:
            raise GoogleCalendarError("Could not access this member's Google Calendar. Reconnect the calendar and try again.") from exc

    def calendars(self, encrypted_refresh_token: str) -> list[dict[str, str | bool]]:
        try:
            access_token = self._access_token(encrypted_refresh_token)
            response = httpx.get("https://www.googleapis.com/calendar/v3/users/me/calendarList", headers={"Authorization": f"Bearer {access_token}"}, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            return [{"id": item["id"], "name": item.get("summary", item["id"]), "primary": bool(item.get("primary")), "access_role": item.get("accessRole", "") } for item in response.json().get("items", [])]
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise GoogleCalendarError("Could not read this member's Google calendars. Reconnect the calendar and try again.") from exc

    def free_busy(self, encrypted_refresh_token: str, calendar_id: str, start: datetime, end: datetime, time_zone: str) -> list[dict[str, str]]:
        try:
            access_token = self._access_token(encrypted_refresh_token)
            response = httpx.post("https://www.googleapis.com/calendar/v3/freeBusy", headers={"Authorization": f"Bearer {access_token}"}, json={
                "timeMin": start.isoformat(), "timeMax": end.isoformat(), "timeZone": time_zone, "items": [{"id": calendar_id}],
            }, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            return list(response.json().get("calendars", {}).get(calendar_id, {}).get("busy", []))
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise GoogleCalendarError("Could not read this member's availability. Reconnect the calendar and try again.") from exc

    def events(self, encrypted_refresh_token: str, calendar_id: str, start: datetime, end: datetime, time_zone: str) -> list[dict[str, str]]:
        """Read event labels and timing only after the member has explicitly consented."""
        try:
            access_token = self._access_token(encrypted_refresh_token)
            response = httpx.get(f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events", headers={"Authorization": f"Bearer {access_token}"}, params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime", "timeZone": time_zone}, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            return [{"title": item.get("summary", "Busy"), "start": item.get("start", {}).get("dateTime", item.get("start", {}).get("date", "")), "end": item.get("end", {}).get("dateTime", item.get("end", {}).get("date", ""))} for item in response.json().get("items", []) if item.get("status") != "cancelled"]
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise GoogleCalendarError("Could not read this member's schedule. Reconnect the calendar and try again.") from exc
