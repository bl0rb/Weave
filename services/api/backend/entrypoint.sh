#!/bin/sh
set -e

db_target=$(python - <<'PY'
import sys

from sqlalchemy.engine import make_url

from app.core.config import settings

try:
    url = make_url(settings.database_url)
except Exception as e:
    print(f"WARN: could not parse DATABASE_URL ({e}); skipping postgres wait.", file=sys.stderr)
    sys.exit(0)

if not url.host:
    print("WARN: DATABASE_URL has no host (e.g. sqlite); skipping postgres wait.", file=sys.stderr)
    sys.exit(0)

print(f"{url.host} {url.port or 5432}")
PY
)

if [ -n "$db_target" ]; then
  db_host=$(echo "$db_target" | cut -d' ' -f1)
  db_port=$(echo "$db_target" | cut -d' ' -f2)
  echo "Waiting for postgres at $db_host:$db_port..."
  until python - "$db_host" "$db_port" <<'PY'
import sys, socket
host = sys.argv[1]
port = int(sys.argv[2])
try:
    s = socket.create_connection((host, port), timeout=2)
    s.close()
except Exception as e:
    print(f"Not ready: {e}", file=sys.stderr)
    sys.exit(1)
PY
  do
    sleep 1
  done
  echo "Postgres is ready."
fi

if [ "${RUN_ALEMBIC_ON_STARTUP:-true}" = "true" ]; then
  echo "Running alembic migrations..."
  alembic upgrade head
else
  echo "Skipping Alembic migrations at startup (RUN_ALEMBIC_ON_STARTUP=false)."
fi

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
