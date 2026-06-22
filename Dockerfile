# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────
# Telegram Assistant — runtime image.
# Python 3.12 slim + ffmpeg (required by audio_service for voice notes) +
# tzdata (Asia/Tashkent). Runs as a non-root user.
# ─────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# System deps:
#   ffmpeg   -> transcode TTS mp3 -> Telegram opus voice notes
#   tzdata   -> IANA timezone database (Asia/Tashkent)
#   libpq5   -> psycopg runtime (sync Postgres driver for APScheduler jobstore)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        tzdata \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Tashkent

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source and migrations.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Persistent media + (optional) sqlite live here; mounted as a volume in compose.
RUN mkdir -p /app/data /app/data/media

# Drop privileges.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# The bot has no inbound ports (it polls Telegram); no EXPOSE needed.
CMD ["python", "-m", "app.main"]
