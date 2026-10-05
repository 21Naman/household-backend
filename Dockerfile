# A plain container image, deliberately host-agnostic.
#
# It was originally written for a Hugging Face Space; Docker Spaces became a
# paid feature in July 2026, so the current target is Render's free tier. The
# only thing that made it Space-specific was a pinned port, and that is gone:
# Render, Koyeb and Cloud Run all inject $PORT and route to whatever the
# process binds there, so Settings.resolved_port() reads it and
# HOUSEHOLD_PORT overrides it only when set deliberately.
#
# Worth knowing about any free tier: the audio cache, the Gnani synthesis
# counter and the APScheduler jobs all live in process memory, and the
# unclosed-loop sweep needs a loop open for six hours before it has anything
# to say. A host that stops the process when idle loses all of that. See
# docs/honest-limits.md.
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

# Run as a non-root user, and keep the SQLite file under /app/data -- which
# is empty in the image, because data/*.db is gitignored and nothing tracks
# it. That is why HOUSEHOLD_DEMO_SEED_ON_STARTUP below is not optional:
# without it the household picker comes up empty on every deploy.
RUN useradd -m -u 1000 user && mkdir -p /app/data && chown -R user:user /app
USER user

# No HOUSEHOLD_PORT on purpose: setting it here would override the $PORT the
# platform injects, the health check would never pass, and the deploy would
# be marked failed with the application running perfectly inside it.
ENV HOUSEHOLD_BIND_HOST=0.0.0.0 \
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

# Informational only -- the real port comes from $PORT at runtime.
EXPOSE 8000

# HOUSEHOLD_API_KEY is deliberately absent. It is supplied by the host as an
# environment secret, and with HOUSEHOLD_PUBLIC_DEPLOYMENT=true and no key
# the container exits during startup with the Ticket #7 message. That is the
# guard working, not a misconfiguration -- it is the whole reason this
# deployment was built.

# `python -m app.main`, NOT `uvicorn app.main:app`. run() carries the
# original bind-host check, and going through it means both that check and
# the lifespan public-deployment guard fire. Starting uvicorn directly skips
# the first one, which is exactly the hole this deployment closes.
CMD ["python", "-m", "app.main"]
