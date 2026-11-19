#!/usr/bin/env bash
# End-to-end test run against a real, isolated stack.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
E2E_VAR="$ROOT/var/e2e"
export SCRIPTORIUM_DATABASE_URL="${E2E_DATABASE_URL:-postgresql+psycopg://scriptorium:scriptorium_dev@localhost:5432/scriptorium_e2e}"
export SCRIPTORIUM_REDIS_URL="${E2E_REDIS_URL:-redis://localhost:6379/14}"
export SCRIPTORIUM_STORAGE_LOCAL_ROOT="$E2E_VAR/storage" SCRIPTORIUM_MAIL_OUTBOX_DIR="$E2E_VAR/outbox"
# Installed model files are content-addressed and shared with the dev store (no re-copy).
# Model cache 1: the whole stack, Chrome and a training run share an 8 GB memory cgroup here.
export SCRIPTORIUM_MODEL_DIR="${E2E_MODEL_DIR:-$ROOT/var/models}" SCRIPTORIUM_PUBLIC_BASE_URL="http://127.0.0.1:3001" SCRIPTORIUM_TRAINING_DIR="$ROOT/var/e2e/training" SCRIPTORIUM_MODEL_CACHE_SIZE="${SCRIPTORIUM_MODEL_CACHE_SIZE:-1}"
export SCRIPTORIUM_CELERY_EAGER=false
export SCRIPTORIUM_CORS_ORIGINS='["http://127.0.0.1:3001"]' E2E_OUTBOX="$E2E_VAR/outbox"

# Malware scanning as in production: a real ClamAV daemon (scripts/clamd-test.sh) when installed.
if [ -z "${E2E_MALWARE_SCANNER:-}" ] && command -v clamd >/dev/null; then E2E_MALWARE_SCANNER=clamd; fi
if [ "${E2E_MALWARE_SCANNER:-none}" = "clamd" ]; then
  "$ROOT/scripts/clamd-test.sh" >/dev/null
  export SCRIPTORIUM_MALWARE_SCANNER=clamd SCRIPTORIUM_CLAMD_ADDRESS="tcp://127.0.0.1:${CLAMD_PORT:-3310}"
fi
export E2E_MALWARE_SCANNER="${E2E_MALWARE_SCANNER:-none}"

rm -rf "$E2E_VAR"; mkdir -p "$E2E_VAR"
PGPASSWORD=scriptorium_dev psql -q -h localhost -U scriptorium -d postgres -c "DROP DATABASE IF EXISTS scriptorium_e2e" -c "CREATE DATABASE scriptorium_e2e"
redis-cli -n 14 flushdb >/dev/null
(cd "$ROOT/backend" && "$VENV/bin/alembic" upgrade head >/dev/null)

# Kraken's bundled segmenter (registered by the worker bootstrap in Docker; here explicitly).
SAMPLES="${E2E_SAMPLES:-$ROOT/var/samples/traprinq-val}"
(cd "$ROOT/backend" && "$VENV/bin/python" -m app.cli register-bundled >/dev/null 2>&1)
# Recognition models are installed by the administrator through the registry UI (e2e/02-registry.spec.ts).
# A real multi-page PDF assembled from manuscript pages of the TraPrInq validation set.
if ls "$SAMPLES"/*.jpg >/dev/null 2>&1; then
  "$VENV/bin/python" -I -c "import sys,glob;from PIL import Image;ims=[Image.open(p).convert('RGB') for p in sorted(glob.glob(sys.argv[1]+'/*.jpg'))];ims[0].save(sys.argv[2],save_all=True,append_images=ims[1:],resolution=150)" "$SAMPLES" "$E2E_VAR/inquisicao-3-folios.pdf"
  export E2E_SAMPLE_JPG="$SAMPLES/PT-TT-TSO-IL-28-7926_m0052.jpg" E2E_SAMPLE_PDF="$E2E_VAR/inquisicao-3-folios.pdf"
fi

stop_stack() {
  # Kill whole process groups (npx spawns next-server as a child).
  for pid in ${API_PID:-} ${WEB_PID:-} ${WORKER_PID:-} ${MODELS_PID:-} ${TRAINING_PID:-}; do kill -- -"$pid" 2>/dev/null || true; done
  pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8001" 2>/dev/null || true
  pkill -f "next start -p 3001" 2>/dev/null || true
  pkill -f "next-server" -P 1 2>/dev/null || true
  pkill -f "celery -A app.worker.celery_app worker.*e2e-worker" 2>/dev/null || true
  pkill -f "e2e-worker-models" 2>/dev/null || true
  pkill -f "e2e-worker-training" 2>/dev/null || true
  pkill -f "app.training.runner" 2>/dev/null || true
  for port in 3001 8001; do fuser -k "$port/tcp" 2>/dev/null || true; done
}
stop_stack; sleep 1
trap stop_stack EXIT
setsid bash -c "cd '$ROOT/backend' && exec '$VENV/bin/uvicorn' app.main:app --host 127.0.0.1 --port 8001" >"$E2E_VAR/api.log" 2>&1 & API_PID=$!
# Two workers, as in production: model downloads must never block ingestion or recognition.
setsid bash -c "cd '$ROOT/backend' && exec '$VENV/bin/celery' -A app.worker.celery_app worker -Q default,recognition -c 1 --max-memory-per-child 1500000 -n e2e-worker@%h --loglevel INFO" >"$E2E_VAR/worker.log" 2>&1 & WORKER_PID=$!
setsid bash -c "cd '$ROOT/backend' && exec '$VENV/bin/celery' -A app.worker.celery_app worker -Q models -c 1 -n e2e-worker-models@%h --loglevel INFO" >"$E2E_VAR/worker-models.log" 2>&1 & MODELS_PID=$!
setsid bash -c "cd '$ROOT/backend' && exec '$VENV/bin/celery' -A app.worker.celery_app worker -Q training -c 1 -n e2e-worker-training@%h --loglevel INFO" >"$E2E_VAR/worker-training.log" 2>&1 & TRAINING_PID=$!
setsid bash -c "cd '$ROOT/frontend' && SCRIPTORIUM_API_ORIGIN=http://127.0.0.1:8001 exec npx next start -p 3001" >"$E2E_VAR/web.log" 2>&1 & WEB_PID=$!
for i in $(seq 60); do curl -sf http://127.0.0.1:3001/api/health/ready >/dev/null && break; sleep 1; done
cd "$ROOT/frontend" && npx playwright test "$@"
