#!/usr/bin/env bash
# Run every automated test suite. Requires local PostgreSQL + Redis (see docs/installation.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
echo "== backend (pytest, real PostgreSQL + Redis + Celery worker) =="
(cd "$ROOT/backend" && "$VENV/bin/python" -m pytest -q --cov=app --cov-report=term:skip-covered "$@")
echo "== frontend (type-check + vitest) =="
(cd "$ROOT/frontend" && npx tsc --noEmit && npx vitest run)
echo "== end-to-end (Playwright + Chrome, real stack) =="
(cd "$ROOT/frontend" && npx next build >/dev/null)
"$ROOT/scripts/e2e.sh"
