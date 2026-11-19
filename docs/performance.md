# Performance

All figures were measured in the Phase 7 build environment: **2 vCPU, 7.8 GB RAM, no GPU**,
PostgreSQL 16 and Redis on the same host, and the load generator running on the same 2 vCPUs as the
API. Absolute numbers on institutional hardware will be higher; the relative findings carry over.
Raw outputs are in `docs/evidence/phase7/`.

## 1. Recognition throughput (real Kraken, CPU)

`scripts/perf/recognition_bench.py` runs the worker's own engine (`app.recognition.engine`):
Kraken 7.1.1 bundled baseline segmenter + TraPrInq V1 on three real 16th-century pages from the
TraPrInq validation set (1280×2179 to 1879×2685 px), two torch threads, model already loaded.

| Stage | Mean per page |
|---|---|
| Baseline segmentation (blla) | 23.3 s |
| Line recognition (TraPrInq) | 13.0 s (2.4 lines/s) |
| **Total** | **36.3 s → ≈ 99 pages/hour per CPU worker** |

The first page after a worker starts also pays model loading (≈ +1 s here, larger for big models).
Segmentation dominates on CPU; it can be skipped when re-recognising a page with another model
(the benchmarking laboratory reuses the stored segmentation: CATMuS jobs on an existing
segmentation took ≈ 5 s in the E2E run). Throughput scales with recognition workers
(`worker` service replicas, each `--concurrency 1`, ≈ 2 GB RAM each) and with a GPU
(`SCRIPTORIUM_RECOGNITION_DEVICE=gpu`). Fine-tuning (Phase 6) measured 91–110 s for the E2E training
set with a 2.1 GB peak.

## 2. API latency under load

`scripts/perf/load_test.py` drives a realistic researcher mix (dashboard, project and document
listings, document detail, transcription load, page images through signed URLs, audit log, and 10 %
real transcription revisions) against `uvicorn --workers 2`. The database is a copy of the E2E
installation enlarged with **labelled synthetic volume** (`scripts/perf/seed_volume.sql`:
10 000 documents, 40 000 pages, 50 000 image-asset rows, 200 000 audit records in one collection);
images and transcriptions requested are the real ones.

### Findings and fixes

The first run (20 virtual users) exposed two N+1 / data-transfer problems:

| Endpoint (single request, 10 k-document collection) | Before | After |
|---|---|---|
| Document listing, 100 per page | 138 ms | **26 ms** — page counts and source assets fetched in two batched queries instead of 2 per document |
| Dashboard | 347 ms | **96 ms** — line-status aggregation joins pages in SQL instead of sending every visible page id from Python |

### Results after the fixes

| Virtual users | Throughput | Errors | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 5 | 36.5 req/s | 0 | 83 ms | 311 ms | 393 ms |
| 20 | 37.7 req/s | 0 | 401 ms | 912 ms | 1 176 ms |
| 20 (before fixes) | 14.9 req/s | 0 | 670 ms | 4 577 ms | 5 473 ms |

At 5 users every operation's p95 is below 0.4 s (transcription save p95 269 ms, page image p95
229 ms). At 20 users the two API processes and the load generator saturate the two CPUs, so latency
grows with queueing while throughput stays flat — capacity, not a fault: no request failed. Concurrent
edits of the same line returned the expected 409 optimistic-locking conflict (1 and 6 occurrences),
never a lost update. Size the API with roughly one process per core and add replicas behind the
proxy for larger reading rooms.

## 3. Database

* **Foreign-key index audit** (`scripts/perf/fk_index_audit.sql`; `fk-audit-before.txt` /
  `fk-audit-after.txt`): 57 single-column foreign keys had no index.
  Migration `7a1c3e9d5b20` adds 28 of them — every column used by joins, cascades, the transcription
  history and the retention reference checks. Columns referencing `users` stay unindexed on purpose
  (accounts are deactivated, never deleted), as do those of tables with a handful of rows (models,
  training jobs, publications).
* `tests/test_migrations.py` now checks that the migrations and ORM models describe exactly the same
  schema (Alembic `compare_metadata`), so future indexes cannot drift.
* Hot queries at archive scale (`scripts/perf/query_bench.py`, medians of 5 runs,
  `query-bench-before.json` / `query-bench-after.json`): all below 25 ms. The retention reference
  check over 50 000 assets takes 24 ms (merge anti-joins over the whole table, by design); the
  page-by-asset lookup switches from a sequential scan to an index scan.

## 4. Upload and ingestion limits

Uploads stream to a spooled temporary file with the size limit enforced while reading, are scanned
by clamd and stored without loading the whole file in memory; page extraction runs on the worker,
so a large PDF never blocks API requests (Phase 2). Configure `client_max_body_size`/`request_body`
in the reverse proxy to at least `SCRIPTORIUM_MAX_UPLOAD_BYTES` (512 MB by default).

## Reproducing

```bash
createdb -T <copy-of-an-installation> scriptorium_perf
psql -d scriptorium_perf -f scripts/perf/seed_volume.sql      # synthetic volume, perf copies only
python scripts/perf/query_bench.py postgresql://…/scriptorium_perf
SCRIPTORIUM_DATABASE_URL=…scriptorium_perf uvicorn app.main:app --workers 2 --port 8003 &
python scripts/perf/load_test.py --base http://127.0.0.1:8003 --email … --password … --users 20 --seconds 60
python scripts/perf/recognition_bench.py var/models/<sha>/traprinq.mlmodel var/samples/traprinq-val/*.jpg
```
