#!/usr/bin/env bash
# Start a local development stack WITHOUT Docker (native PostgreSQL 16 + Redis 7).
# API :8000 · worker · web :3000. Logs in var/logs/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
LOGS="$ROOT/var/logs"; mkdir -p "$LOGS"

pg_isready -q -h localhost || pg_ctlcluster 16 main start
redis-cli ping >/dev/null 2>&1 || { mkdir -p /var/lib/redis; redis-server --daemonize yes --appendonly yes --dir /var/lib/redis; }

(cd "$ROOT/backend" && "$VENV/bin/alembic" upgrade head)
(cd "$ROOT/backend" && "$VENV/bin/python" -m app.cli register-bundled >/dev/null 2>&1) || echo "warning: bundled segmenter not registered"

pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "celery -A app.worker.celery_app" 2>/dev/null || true
(cd "$ROOT/backend" && nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 >"$LOGS/api.log" 2>&1 &)
(cd "$ROOT/backend" && nohup "$VENV/bin/celery" -A app.worker.celery_app worker -B -Q default,recognition \
    --concurrency 1 -n worker@%h --loglevel INFO --schedule "$ROOT/var/celerybeat-schedule" >"$LOGS/worker.log" 2>&1 &)
# Separate worker for model downloads/validation so they never delay ingestion or recognition.
(cd "$ROOT/backend" && nohup "$VENV/bin/celery" -A app.worker.celery_app worker -Q models \
    --concurrency 1 -n models@%h --loglevel INFO >"$LOGS/worker-models.log" 2>&1 &)
(cd "$ROOT/backend" && nohup "$VENV/bin/celery" -A app.worker.celery_app worker -Q training \
    --concurrency 1 -n training@%h --loglevel INFO >"$LOGS/worker-training.log" 2>&1 &)
if [ "${1:-}" = "--dev" ]; then
  (cd "$ROOT/frontend" && nohup npx next dev -p 3000 >"$LOGS/web.log" 2>&1 &)
else
  [ -d "$ROOT/frontend/.next" ] || (cd "$ROOT/frontend" && npx next build)
  (cd "$ROOT/frontend" && nohup npx next start -p 3000 >"$LOGS/web.log" 2>&1 &)
fi
for i in $(seq 60); do curl -sf http://127.0.0.1:3000/api/health/ready >/dev/null && break; sleep 1; done
curl -s http://127.0.0.1:3000/api/health/ready; echo
echo "SCRIPTORIUM running: http://127.0.0.1:3000  (API docs: http://127.0.0.1:8000/api/docs)"
