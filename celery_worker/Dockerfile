# Celery worker / beat image.
#
# Kept separate from the API image because Phase 4 adds Playwright + headless
# Chromium here, which has no business inflating the web container.
#
# Build context is the repo root so this file can copy from ./backend.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi:app \
    C_FORCE_ROOT=0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/ .

RUN chmod +x /app/worker-entrypoint.sh \
    && adduser --disabled-password --gecos "" --uid 10001 webguard \
    && mkdir -p /var/lib/celery \
    && chown -R webguard:webguard /app /var/lib/celery
USER webguard

ENTRYPOINT ["/app/worker-entrypoint.sh"]
CMD ["celery", "-A", "celery_main:celery", "worker", "--loglevel=info", "--queues=probes,scans", "--concurrency=4"]
