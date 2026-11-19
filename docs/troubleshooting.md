# Troubleshooting

Start with the two diagnostics that cover most problems:

```bash
curl -s https://<host>/api/health/ready | jq      # database, redis, storage, malware_scanner
python -m app.cli security-check                   # (in the api container) configuration + clamd ping
```

Every API error body contains a stable `code` and a `request_id`; search the JSON logs for that id
(`docker compose logs api worker | grep <request_id>`).

## Startup

| Symptom | Cause | Fix |
|---|---|---|
| API exits: `SCRIPTORIUM_SECRET_KEY must be set…` | production refuses the development or a short secret | set a ≥ 32-character random value in `deploy/.env` |
| API exits: `COOKIE_SECURE must be true` / `MAIL_BACKEND=smtp` / `CORS_ORIGINS must not contain '*'` | production guards | fix the setting; these are deliberate |
| `migrate` fails | database unreachable or a migration error | `docker compose logs migrate`; never edit the schema by hand — restore from backup if a migration was interrupted |
| API never starts in Docker | it waits for `clamav` to become healthy (first signature download takes minutes) | `docker compose logs clamav`; check outbound access to the ClamAV mirrors |
| `/ready` → `"redis": error` | Redis down or wrong password | check `REDIS_PASSWORD` in both Redis and backend environment |

## Uploads

| Error code | Meaning | Action |
|---|---|---|
| `malware_detected` (422) | ClamAV matched a signature (named in `details.signature`) | the file is not stored; obtain a clean copy. The event is in the audit log |
| `malware_scanner_unavailable` (503) | clamd unreachable / erroring; uploads fail closed | restore the `clamav` service (`/ready` shows the error). Do not disable scanning in production |
| `file_too_large` (413) | above the institution or platform limit (`details.limit_bytes`) | split the PDF or ask the institution administrator; also check the proxy body limit (a 413 *without* a JSON body comes from the proxy) |
| `storage_quota_exceeded` (413) | institution originals quota reached | institution administrator raises the quota or removes documents |
| `type_not_allowed_by_policy` / `unsupported_type` / `type_mismatch` (415) | type not accepted, not PNG/JPEG/TIFF/PDF, or extension disagrees with content | convert or rename correctly |
| document stays *Processing*, then *Failed* with `image_too_large` / `too_many_pages` | decompression-bomb guard / page limit | `SCRIPTORIUM_MAX_IMAGE_PIXELS`, `SCRIPTORIUM_MAX_PAGES_PER_DOCUMENT` |

## Recognition jobs

| Symptom | Cause | Fix |
|---|---|---|
| jobs stay *Queued* | no recognition worker consuming `default,recognition` | `docker compose ps worker`; `celery -A app.worker.celery_app inspect active_queues` |
| job *Failed* `model_not_validated` / `model_checksum_mismatch` | model file changed or never validated | re-validate or reinstall the model (Models page); the SHA-256 is checked before every load |
| job *Failed* `worker_lost` after retries | worker killed repeatedly (usually out of memory) | give the worker more memory (≥ 4 GB) or reduce `SCRIPTORIUM_MODEL_CACHE_SIZE` |
| job re-queued with "worker lost (no heartbeat)" in its log | a worker died mid-page; the recovery task re-queued it | nothing to do unless it repeats |
| very slow pages | CPU segmentation ≈ 20–25 s per page (docs/performance.md) | add worker replicas or a GPU; reuse an existing segmentation when re-recognising |
| lines in the wrong order or merged | layout of the page | correct the segmentation in the workspace (*Layout* mode) and re-recognise on it |

## Models

| Symptom | Fix |
|---|---|
| *Install from Zenodo* fails with `zenodo_unreachable` (502) | the models worker needs HTTPS to zenodo.org; Zenodo also returns intermittent 5xx (retried automatically) — try again later, or set `SCRIPTORIUM_ZENODO_TOKEN` against rate limits |
| `license_not_accepted` | tick the licence confirmation after reading it |
| status *Incompatible* | the file is not a Kraken recognition model for this Kraken version (the installation log says why — e.g. a segmentation network) |
| *Update available* is shown but nothing changes | updates are never applied silently: install the new version, then *Activate* it |

## Training

| Symptom | Fix |
|---|---|
| `no_training_data` / `no_validation_data` (422) | the dataset needs pages in both the train and the validation split |
| job fails with `out_of_memory` | the training worker was killed by the kernel; give it more memory (≥ 4 GB) or train on a GPU |
| test CER missing on a finished job | the evaluation on the held-out test split runs after training; it appears when that job completes |

## Exports

| Symptom | Fix |
|---|---|
| `nothing_to_export` | no page in scope has a transcription or recognition yet |
| `export_expired` (410) | the archive was removed by the retention policy; request the export again |
| validation badge shows *invalid* | the XML failed schema validation; the details list the errors (please report them with the request id) |

## Sign-in

| Symptom | Fix |
|---|---|
| 429 after several attempts | account temporarily locked (`SCRIPTORIUM_LOGIN_MAX_FAILURES`, `…_LOCKOUT_SECONDS`); wait or reset the password |
| `registration_closed` | self-registration is disabled: ask an administrator for an invitation |
| invitation or reset link rejected | links are single-use; invitations expire after 7 days, resets after 60 minutes — send a new one |
| logged out every 15 minutes behind a proxy | the refresh cookie is not reaching the API: the proxy must forward `/api/*` to the web service on the same origin and keep cookies; `SCRIPTORIUM_PUBLIC_BASE_URL` must match the browser URL |

## Restores

`scripts/restore.sh` refuses non-empty databases and aborts on any checksum mismatch before writing
anything; see [backup-recovery.md](backup-recovery.md).
