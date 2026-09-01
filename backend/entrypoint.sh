#!/usr/bin/env bash
set -euo pipefail

# Postgres accepts TCP before it is ready to serve; wait for a real query.
echo "[entrypoint] waiting for database..."
python - <<'PY'
import os, sys, time
import sqlalchemy as sa

url = os.environ["DATABASE_URL"]
for attempt in range(1, 61):
    try:
        sa.create_engine(url).connect().close()
        print(f"[entrypoint] database ready (attempt {attempt})")
        sys.exit(0)
    except Exception as exc:
        print(f"[entrypoint] not ready ({attempt}/60): {exc.__class__.__name__}")
        time.sleep(2)
print("[entrypoint] database never became ready", file=sys.stderr)
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying migrations..."
    flask db upgrade
fi

exec "$@"
