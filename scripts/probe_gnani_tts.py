"""Measure the Gnani TTS contract against the live API. Run manually.

This exists because two revisions of app/providers/voice_gnani.py shipped a
wrong request body and nothing caught either one -- there was no credential to
try them with. Offline tests pin the body's *shape*; only this tells you the
API accepts it.

It spends real credits, so it is a script you run deliberately, not a test.

    HOUSEHOLD_GNANI_API_KEY=... python scripts/probe_gnani_tts.py
    HOUSEHOLD_GNANI_API_KEY=... python scripts/probe_gnani_tts.py --lengths

Order matters. The auth header is checked first and nothing else runs until it
passes: a wrong header fails every later probe for a reason that has nothing to
do with what is being measured. The original code sent
`Authorization: Bearer`, which would have made a length probe look like a hard
limit of zero characters.

What it answers:
  1. Does the X-API-Key-ID header authenticate?
  2. Does the request body as this codebase builds it return 200 audio?
  3. Where does the practical text limit actually sit? (--lengths)
  4. What does an error body really look like?

Feed the answer to (3) into HOUSEHOLD_GNANI_TTS_MAX_CHARS and update
docs/honest-limits.md, which currently records that number as a guess.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.enums import Language
from app.settings import Settings

# Real Hinglish, in the register the briefing actually produces, so a length
# result reflects this feature rather than a lorem-ipsum string.
SAMPLE = (
    "Aaj hum Tomato Dal Rice bana rahe hain, do logon ke liye, lagbhag pachchees "
    "minute mein. Do sau gram chawal aur sava sau gram moong dal nikaal lijiye. "
    "Tamatar ghar mein nahin hai, pehle wo mangwa lijiye. Chawal aur dal ko "
    "achchhe se dho lijiye, phir pressure cooker mein daal dijiye. "
)


def _client(settings: Settings) -> tuple[str, dict[str, str]]:
    return (
        f"{settings.gnani_base_url.rstrip('/')}/tts/inference",
        {"X-API-Key-ID": settings.gnani_api_key or "", "Content-Type": "application/json"},
    )


def _body(settings: Settings, text: str) -> dict:
    """Exactly what app/providers/voice_gnani.py sends. Do not 'simplify' this
    -- a probe that tests a different body than production proves nothing."""
    return {
        "text": text,
        "voice": settings.gnani_voice_map[Language.HI_EN.value],
        "model": settings.gnani_tts_model,
        "language": Language.HI_EN.value,
        "speed": settings.gnani_tts_speed,
        "audio_config": settings.gnani_audio_config(),
    }


def _describe(response: httpx.Response) -> str:
    if response.status_code == 200:
        return (
            f"200 OK  content-type={response.headers.get('content-type')}  "
            f"{len(response.content)} bytes"
        )
    try:
        payload = response.json()
        error = payload.get("error", {})
        return f"{response.status_code}  type={error.get('type')}  message={error.get('message')}"
    except Exception:  # noqa: BLE001
        return f"{response.status_code}  (non-JSON body) {response.content[:160]!r}"


def probe_auth(settings: Settings) -> bool:
    url, headers = _client(settings)
    print("\n[1] Auth header — X-API-Key-ID")
    print(f"    POST {url}")
    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=_body(settings, "Namaste, aap kaise hain?"), headers=headers)
    print(f"    -> {_describe(response)}")
    if response.status_code == 200:
        print("    PASS: the header authenticates and the body is accepted.")
        out = Path("scripts/probe_output.audio")
        out.write_bytes(response.content)
        print(f"    Wrote {out} — play it to confirm the voice and language are right.")
        return True
    if response.status_code in (401, 403):
        print("    FAIL: authentication or permission. Check the key, its scope and credits.")
    else:
        print("    FAIL: see the error above. Fix this before probing anything else.")
    return False


def probe_lengths(settings: Settings) -> None:
    url, headers = _client(settings)
    print("\n[3] Practical text limit")
    print("    HOUSEHOLD_GNANI_TTS_MAX_CHARS is currently a guess "
          f"({settings.gnani_tts_max_chars}). This measures it.")
    last_ok = 0
    with httpx.Client(timeout=180.0) as client:
        for target in (500, 1000, 1500, 2000, 3000, 5000):
            text = (SAMPLE * ((target // len(SAMPLE)) + 1))[:target]
            response = client.post(url, json=_body(settings, text), headers=headers)
            print(f"    {target:>5} chars -> {_describe(response)}")
            if response.status_code == 200:
                last_ok = target
            else:
                print(f"\n    Largest accepted size: {last_ok} chars.")
                print(f"    Set HOUSEHOLD_GNANI_TTS_MAX_CHARS below that, then update "
                      f"docs/honest-limits.md to say it was measured, not assumed.")
                return
    print(f"\n    No limit hit up to {last_ok} chars. Record that ceiling as "
          "'tested to N, not a documented limit' rather than claiming there is none.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lengths", action="store_true",
                        help="also measure the text limit (several extra billable calls)")
    args = parser.parse_args()

    settings = Settings()
    if not settings.gnani_api_key:
        print("HOUSEHOLD_GNANI_API_KEY is not set. Add it to .env or pass it inline.",
              file=sys.stderr)
        return 2

    print(f"base_url     {settings.gnani_base_url}")
    print(f"model        {settings.gnani_tts_model}")
    print(f"language     {Language.HI_EN.value} ({Language.HI_EN.display_name})")
    print(f"voice        {settings.gnani_voice_map.get(Language.HI_EN.value)}")
    print(f"audio_config {settings.gnani_audio_config()}")
    print(f"speed        {settings.gnani_tts_speed}  (top-level, not in audio_config)")

    if not probe_auth(settings):
        return 1
    if args.lengths:
        probe_lengths(settings)
    else:
        print("\n[3] Text limit not probed. Re-run with --lengths once auth is confirmed.")
    print("\nDone. Update docs/honest-limits.md with what this actually proved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
