# Hugging Face Spaces, Docker SDK, free CPU tier.
#
# Chosen over the alternatives because a free Space sleeps after ~48h idle
# rather than ~15 minutes. That matters more here than it would for most
# apps: the audio cache, the Gnani synthesis counter and the APScheduler
# jobs all live in process memory, and the unclosed-loop sweep only has
# something to say about a loop that has been open for six hours. On a
# fifteen-minute-idle host the sweep would never fire once and a reader
# returning to an open tab would get a 404 on playback.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

# Spaces runs the container as uid 1000, and the SQLite file lives under
# /app/data -- which is empty in the image, because data/*.db is gitignored
# and nothing tracks it. That is why HOUSEHOLD_DEMO_SEED_ON_STARTUP below is
# not optional: without it the household picker comes up empty.
RUN useradd -m -u 1000 user && mkdir -p /app/data && chown -R user:user /app
USER user

ENV HOUSEHOLD_BIND_HOST=0.0.0.0 \
    HOUSEHOLD_PORT=7860 \
    HOUSEHOLD_PUBLIC_DEPLOYMENT=true \
    HOUSEHOLD_DEMO_SEED_ON_STARTUP=true \
    HOUSEHOLD_DATABASE_URL=sqlite:////app/data/household.db \
    # Live Gnani TTS. The flag defaults to true, and the live rail requires
    # a key AND the mock explicitly off -- so without this line a Space with
    # a perfectly good GNANI key would still play a synthetic tone, and only
    # a startup warning would say so. Playback spends real vendor credits;
    # the per-household daily cap is per-process, not global (see
    # docs/honest-limits.md).
    HOUSEHOLD_GNANI_MOCK_ENABLED=false \
    # Tuned for a cook standing at a stove, not a reader working through the
    # page at their own pace. A named setting, changed here rather than in
    # the default, so the local build keeps the value it was designed for.
    HOUSEHOLD_RECIPE_AUDIO_TTL_SECONDS=14400

EXPOSE 7860

# HOUSEHOLD_API_KEY is deliberately absent. It comes from a Space secret, and
# with HOUSEHOLD_PUBLIC_DEPLOYMENT=true and no key the container exits during
# startup with the Ticket #7 message. That is the guard working, not a
# misconfiguration -- it is the whole reason this deployment was built.

# `python -m app.main`, NOT `uvicorn app.main:app`. run() carries the
# original bind-host check, and going through it means both that check and
# the lifespan public-deployment guard fire. Starting uvicorn directly skips
# the first one, which is exactly the hole this deployment closes.
CMD ["python", "-m", "app.main"]
