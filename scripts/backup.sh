#!/usr/bin/env bash
# SCRIPTORIUM backup: database (pg_dump), storage objects (local or S3) and installed model files,
# with a manifest and SHA256SUMS. Uses the same SCRIPTORIUM_* environment as the application.
#   scripts/backup.sh /backups              # → /backups/scriptorium-backup-<UTC timestamp>/
#   KEEP=14 scripts/backup.sh /backups      # also prune sets beyond the 14 most recent
# In Docker:  docker compose exec -T api python -m app.cli backup --dest /backups
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:?usage: backup.sh <destination directory>}"
mkdir -p "$DEST"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
(cd "$ROOT/backend" && "$PY" -m app.cli backup --dest "$DEST")
if [ -n "${KEEP:-}" ]; then
  ls -1dt "$DEST"/scriptorium-backup-* | tail -n +"$((KEEP + 1))" | xargs -r rm -rf
fi
