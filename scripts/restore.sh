#!/usr/bin/env bash
# SCRIPTORIUM restore into a NEW, EMPTY database and storage location.
#   SCRIPTORIUM_DATABASE_URL=postgresql+psycopg://…/scriptorium_restored \
#   SCRIPTORIUM_STORAGE_LOCAL_ROOT=/srv/scriptorium/storage SCRIPTORIUM_MODEL_DIR=/srv/scriptorium/models \
#   scripts/restore.sh /backups/scriptorium-backup-20261119T010000Z
# All checksums are verified before anything is written; afterwards every image asset and model
# file is re-hashed and the research-data fingerprint is compared with the one taken at backup time.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:?usage: restore.sh <backup set directory>}"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
(cd "$ROOT/backend" && "$PY" -m app.cli restore --src "$(cd "$SRC" && pwd)")
