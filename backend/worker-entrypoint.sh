#!/usr/bin/env bash
set -euo pipefail

# Workers never migrate — the API container owns the schema. They only need to
# wait until it exists, or the first task fails on a missing table.
echo "[worker] waiting for database and broker..."
python - <<'PY'
import os, sys, time
import sqlalchemy as sa
import redis as redis_lib

db_url = os.environ["DATABASE_URL"]
redis_url = os.environ["REDIS_URL"]

for attempt in range(1, 61):
    try:
        sa.create_engine(db_url).connect().close()
        redis_lib.from_url(redis_url).ping()
        print(f"[worker] dependencies ready (attempt {attempt})")
        sys.exit(0)
    except Exception as exc:
        print(f"[worker] not ready ({attempt}/60): {exc.__class__.__name__}")
        time.sleep(2)
print("[worker] dependencies never became ready", file=sys.stderr)
sys.exit(1)
PY

exec "$@"
