# Backup and recovery

SCRIPTORIUM keeps research data in three places, and a usable backup needs all three at a
consistent point:

| Data | Where | Notes |
|---|---|---|
| Metadata, transcriptions, revisions, ground truth, evaluation results, audit trail | PostgreSQL | immutable research tables are protected by triggers |
| Uploaded originals, page images, preprocessed derivatives, export archives, publication packages | object storage (local directory or S3/MinIO) | content-addressed: `namespace/ab/<sha256>` |
| Installed recognition/segmentation models | `SCRIPTORIUM_MODEL_DIR` | paths and SHA-256 recorded in `model_versions` |

Redis holds only queues, rate-limit counters and task results; it is not part of the backup set
(see the recovery table for queued jobs). Training working directories
(`SCRIPTORIUM_TRAINING_DIR`) hold intermediate checkpoints; the resulting models are registered and
included through the model directory.

## Making a backup

```bash
# Native installation (uses the same SCRIPTORIUM_* environment as the services)
scripts/backup.sh /backups                 # → /backups/scriptorium-backup-<UTC timestamp>/
KEEP=14 scripts/backup.sh /backups         # keep only the 14 most recent sets

# Docker deployment (mount a backup volume into the api service first)
docker compose -f deploy/docker-compose.yml exec -T api python -m app.cli backup --dest /backups
```

A backup set contains:

```
database.dump      pg_dump --format=custom (no owner/ACL statements → restorable under any role)
objects/<key>      every storage object, each verified against its content address while copied
models/<sha>/…     every model file referenced by the database
manifest.json      versions, schema revision, counts and a research-data fingerprint
SHA256SUMS         checksum of every file (compatible with `sha256sum -c`)
```

Consistency: the database is dumped first, in one MVCC snapshot. Objects are immutable and
written before the rows that reference them, so every object referenced by the dump already exists
when the objects are copied. Do not schedule the retention run (`maintenance.retention`) at the same
time as a backup.

Recommended policy: daily sets, 14 retained locally, copied off-site **encrypted** (e.g.
`restic`, `borg`, or the institution's backup system) — backups contain unpublished research
material and personal data of users. Test a restore at least quarterly.

## Verifying a backup without restoring it

```bash
(cd backend && ../.venv/bin/python -m app.cli verify-backup --src /backups/scriptorium-backup-…)
# or: cd /backups/scriptorium-backup-… && sha256sum -c SHA256SUMS
```

## Restoring

Restore always goes into a **new, empty database** (the command refuses a non-empty one) and a
storage location of your choice:

```bash
createdb -O scriptorium scriptorium_restored
export SCRIPTORIUM_DATABASE_URL=postgresql+psycopg://scriptorium:…@db-host/scriptorium_restored
export SCRIPTORIUM_STORAGE_LOCAL_ROOT=/srv/scriptorium/storage     # or the S3 settings
export SCRIPTORIUM_MODEL_DIR=/srv/scriptorium/models
scripts/restore.sh /backups/scriptorium-backup-20261119T003958Z
```

The restore:

1. verifies **every** checksum in `SHA256SUMS` before writing anything (a single altered byte aborts);
2. restores the database dump (schema revision included — run `alembic upgrade head` afterwards only
   when restoring into a newer SCRIPTORIUM version);
3. writes every object back through the storage backend and checks that its key comes out identical;
4. copies the model files and rewrites their paths in `model_versions`;
5. re-hashes every image asset and model file against the SHA-256 stored in the restored database;
6. recomputes the research-data fingerprint (counts and content hashes of documents, recognitions,
   revisions, ground truth, evaluation CERs, models, audit trail) and fails if it differs from the
   one recorded at backup time.

Then point the services at the restored database and storage and start them. Users keep their
passwords; sessions remain valid only if `SCRIPTORIUM_SECRET_KEY` is unchanged.

## Recovery scenarios

| Situation | Action |
|---|---|
| API/worker container lost | Recreate it; no data lives in application containers. |
| Worker killed during recognition | Automatic: stale `PROCESSING` jobs are re-queued (`jobs.recover`, every 60 s). |
| Redis lost | Restart Redis (AOF persistence is enabled, so queued messages normally survive). If the Redis data itself was lost, jobs still shown as *Queued* have no broker message: cancel and resubmit them from the job list. Jobs that were *Processing* are re-queued automatically by the recovery task. |
| Object storage lost or corrupted | Restore the latest set into a new database + storage location, or copy back `objects/` only (keys are content addresses, so a partial copy can be verified with `verify_restored`). |
| Database lost | Full restore from the latest set. Changes after that set are lost — schedule backups to match the acceptable data loss (RPO). |
| Accidental deletion of a document | Restore the set into a separate database and storage, export the document from there, re-import it. Deleted documents' files are kept for `SCRIPTORIUM_ORPHAN_GRACE_DAYS` (default 7) before the retention run removes them. |

## Evidence (Phase 7)

`docs/evidence/phase7/backup-run.json`, `restore-run.txt` and `restored-api-check.txt` record a real
round trip of the end-to-end test installation (13 objects, 211 MB, 5 model files, 10 image assets):
tamper detection, restore into a new database (fingerprint identical), refusal of a non-empty
target, and the API serving the restored data with every page image re-verified by SHA-256.
`tests/test_phase7.py::test_backup_restore_round_trip_is_verified_and_tamper_evident` repeats the
round trip in the automated suite.
