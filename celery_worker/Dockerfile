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

COPY backend/requirements.txt backend/requirements-worker.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install -r requirements-worker.txt

# Chromium's runtime libraries, installed by hand rather than with
# `playwright install --with-deps`: this base is Debian bookworm, Playwright
# falls back to its Ubuntu 20.04 package list, and two of those font packages
# (ttf-unifont, ttf-ubuntu-font-family) do not exist in Debian — the whole
# install then aborts. These are the same shared objects, named for Debian.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
        libcups2 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libx11-6 libxcb1 libxext6 libglib2.0-0 \
        libpango-1.0-0 libcairo2 libasound2 fonts-liberation \
    && playwright install chromium \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .

RUN chmod +x /app/worker-entrypoint.sh \
    && adduser --disabled-password --gecos "" --uid 10001 webguard \
    # Docker seeds a fresh named volume from these paths, ownership included,
    # so beat can write its schedule and the browser can write screenshots.
    && mkdir -p /var/lib/celery /app/artifacts \
    && chown -R webguard:webguard /app /var/lib/celery /opt/playwright
USER webguard

ENTRYPOINT ["/app/worker-entrypoint.sh"]
CMD ["celery", "-A", "celery_main:celery", "worker", "--loglevel=info", "--queues=probes,scans", "--concurrency=4"]
