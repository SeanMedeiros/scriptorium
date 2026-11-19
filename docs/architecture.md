# SCRIPTORIUM — Architecture

Status: accepted (Phase 1); extended in Phases 2–7 (see docs/reports/). This document records the architecture and the
decisions taken after inspecting the build environment. It is updated at the
end of every phase.

## 1. Environment inspection (2026-11-17)

| Item | Finding | Consequence |
|---|---|---|
| OS / CPU / RAM | Ubuntu 24.04, 2 vCPU, 7.8 GB, **no GPU** | Kraken runs on CPU; worker concurrency default = 1 |
| Python | 3.12 / 3.13 | Platform pinned to **Python 3.12** (widest wheel coverage for kraken/coremltools) |
| Kraken | **7.1.1** from PyPI (torch 2.14) | Installs and runs; `kraken --version` verified |
| Node | 22.x, npm 10 | Next.js frontend |
| PostgreSQL | 16 (native) | Primary datastore |
| Redis | 7.0 (native) | Celery broker/result backend, login throttling |
| Docker | Engine runs, **Docker Hub blocked** | Compose/Dockerfiles authored for deployment, image build **not verifiable here** |
| zenodo.org / doi.org / huggingface.co | **Blocked by egress proxy** | Model download (Phases 2–3) requires the domain to be allow-listed; no simulation is used in the meantime |
| MinIO | not installed, binary host blocked | Storage abstraction with a verified local filesystem backend; S3/MinIO backend implemented with boto3, unit-tested against moto (explicitly marked), MinIO used in Compose |
| Browser | google-chrome present | Playwright end-to-end tests possible |

## 2. Logical architecture

```
                ┌────────────────────────────┐
  Browser ───▶  │  Next.js frontend (TS)     │  /api/* rewritten to the API (same origin)
                └─────────────┬──────────────┘
                              │ REST/JSON (OpenAPI)
                ┌─────────────▼──────────────┐
                │  FastAPI API  (app.api)     │  auth, RBAC, projects, documents, registry, exports
                └──┬──────────┬───────────┬──┘
                   │          │           │
          SQLAlchemy│    Celery│      Storage│ (local FS | S3/MinIO)
                   ▼          ▼           ▼
            PostgreSQL     Redis     Object storage (originals are write-once)
                              │
                ┌─────────────▼──────────────┐
                │ Worker (app.worker)         │  Kraken engine (app.recognition),
                │  queues: recognition,       │  model installer (app.registry),
                │          models, default    │  exports
                └─────────────────────────────┘
```

Layer independence rules:

* `app.recognition` (Kraken) is imported **only** by the worker. The API image
  does not need PyTorch.
* `app.registry` (Zenodo/model management) talks to the network and to the
  model directory; it never imports the web layer.
* `app.storage` is a protocol with interchangeable backends.
* The frontend only talks to the documented REST API.

## 3. Repository layout

```
backend/            Python package `app` (API, worker, recognition, registry, storage)
  alembic/          versioned schema migrations
  tests/            pytest (real PostgreSQL + Redis; fixtures are labelled)
frontend/           Next.js 15 + React + TypeScript + Tailwind
deploy/             Dockerfiles, docker-compose.yml, .env.example
docs/               architecture, guides, phase reports
scripts/            dev helpers (start/stop services, seed admin)
```

## 4. Key decisions

1. **Authentication** – Argon2id password hashes. Short-lived JWT access token
   (15 min, held in memory by the frontend) + opaque refresh token in an
   `HttpOnly; SameSite=Lax` cookie scoped to `/api/v1/auth`. Refresh tokens are
   stored **hashed** in `user_sessions`, rotated on every refresh, and revocable
   (session management). Reuse of a rotated token revokes the whole session
   family.
2. **Password reset** – single-use random token, stored hashed, 60-minute
   expiry. Delivery through a pluggable mail backend: `smtp` (production) or
   `file` outbox (development/testing, writes RFC-822 `.eml` files). No
   account enumeration in responses.
3. **Authorization** – three scopes:
   * platform: `is_platform_admin`;
   * institution: `institution_admin` | `member`;
   * project: `principal_investigator`, `researcher`, `transcription_editor`,
     `reviewer`, `read_only`.
   Permissions are computed centrally in `app.core.permissions`; every query
   that lists data is filtered by membership, giving institution-level
   isolation. Independent researchers own projects without an institution.
4. **Audit** – append-only `audit_logs` table written in the same transaction
   as the change it records.
5. **Storage** – content-addressed (SHA-256) keys; originals are written once
   and never overwritten; derived files get new keys.
6. **IDs** – UUIDv4 primary keys (safe to expose, mergeable across instances).
7. **Time** – all timestamps `timestamptz`, UTC.
8. **Logging** – JSON lines on stdout with request id, user id and latency.
9. **Configuration** – 12-factor, environment variables (`SCRIPTORIUM_*`),
   validated by pydantic-settings; production refuses to start with the
   default secret key.
10. **Background jobs** – Celery with Redis; separate queues so model
    downloads/training never block recognition; `worker_prefetch_multiplier=1`
    and `acks_late` for long CPU jobs.
11. **Privacy** – no document is sent to third-party AI APIs; recognition is
    local Kraken only.

## 5. Data model (Phase 1 subset)

`institutions`, `users`, `institution_memberships`, `user_sessions`,
`password_reset_tokens`, `projects`, `project_memberships`, `collections`,
`audit_logs`. Later phases add documents, pages, image assets, regions,
lines, transcriptions, revisions, models, model versions, licenses, jobs,
evaluation datasets/results and annotations through new Alembic migrations.

## 6. Phase 2 additions

* Recognition engine `app.recognition` (Kraken 7 task API), imported only by workers.
* Immutable assets and recognition outputs, enforced by database triggers.
* Jobs: atomic claim, heartbeat, single-transaction output write, stale-job recovery
  (worker start + Celery beat every 60 s), maximum 3 attempts.
* Signed, expiring URLs for images (HMAC-SHA256 over asset id + expiry).
* Model store: content-addressed read-only files; status `validated` only after a real Kraken load.

## 7. Phase 3 additions

* `app.registry.zenodo` (HTTP client, model-card parsing against htrmopo schemas, pre-check)
  and `app.registry.manager` (preview, install, versions, updates, discovery, recommendation).
* Installation runs on the `models` queue; daily `models.check_updates` beat task (reports only).
* Model identity = Zenodo concept record; one `model_versions` row per installed record/file.
* Source facts and administrator curation are stored in separate fields and labelled in the UI.

## 8. Phase 4 additions — transcription workspace

* `app.editorial`: markup v1 (`markup.py`, parser/validator/renderings, mirrored in
  `frontend/src/lib/markup.ts`), transcription policies (`policy.py`), revision service
  (`service.py`).
* Layers kept apart (master prompt §9): `line_recognitions` (original HTR, immutable) →
  `line_versions.text` (researcher correction, with markup) → `line_versions.normalized_text`
  (editorial normalisation) → expanded abbreviations (a *rendering* of `{abbr|expansion}`,
  never stored or applied automatically) → `line_versions.note` and `annotations`.
* Revision model: `transcriptions` (one per recognition, policy copied at creation) →
  `transcription_revisions` (numbered, kind `initial|edit|restore|review`, author, message,
  policy snapshot) → `line_versions` (only the lines changed in that revision). The current
  text of a line is the newest version (`DISTINCT ON line_id`); any past state is
  reconstructed with `at_revision`. Both tables are immutable (trigger); undo and restore
  create new revisions — history is never rewritten.
* Concurrency: every change carries `expected_version_id`; the transcription row is locked
  (`FOR UPDATE`) and a stale version yields HTTP 409 `edit_conflict` with the current text,
  shown in the editor as an explicit choice (mine / theirs).
* Line status: `unreviewed → corrected → reviewed`; review requires the `review` permission;
  editing a reviewed line returns it to `corrected`.
* Layout adjustment: a researcher may delete/add/redraw lines (baselines); this creates a new
  derived `segmentations` row (manual, parent recorded); polygons are computed by Kraken and the
  page is re-recognised as a normal job — the previous segmentation and its outputs remain.
* Workers: model installations run on a dedicated `models` worker so that a 160 MB download never
  blocks ingestion or recognition (found during Phase 4 E2E).

## 9. Phase 5 additions — research tools

* `app.evaluation`: `metrics` (methodology `scriptorium-metrics/1`, rapidfuzz Levenshtein,
  cross-checked by a reference implementation in tests), `gt_import` (ALTO/PAGE), `service`
  (datasets, leakage checks by page and image SHA-256, line-aligned scoring, aggregation by 10
  dimensions). Tables: `evaluation_datasets`, `ground_truth_pages`/`_lines`/`_withdrawals`,
  `evaluation_runs`, `evaluation_results` (immutable where stated).
* Evaluation runs and comparisons are ordinary `recognize` jobs on a fixed input segmentation
  (`app.services.jobs`), so image, segmentation and model are identical across models; the worker
  scores evaluation jobs after committing the recognition.
* `comparisons` + `transcriptions.origin = combined` and `line_versions.origin_line_recognition_id`
  keep the origin of every chosen line.
* `app.exports`: `context` (layers, provenance, quality), `formats` (8 builders), `validate`
  (ALTO 4.4 XSD, PAGE 2019 XSD, TEI tei_all RNG, hOCR structural check), `batch` (ZIP + manifest,
  Celery task `exports.build` on the default queue, stored write-once).
* Reports: `/projects/{id}/report` (JSON/CSV), `/institutions/{id}/report`; the dashboard quality
  block uses stored evaluation results only.

## 10. Phase 6 additions — advanced features

* `app.training`: `service` (request checks, PAGE data preparation, orchestration, registration,
  checkpoints, stale-job recovery) and `runner` (separate process reproducing `ketos train` with
  Kraken's API plus an event callback). Dedicated Celery queue `training` and `training-worker`
  service; heartbeat every 2 s; a lost worker marks the job failed (never silently restarted).
* Tables: `training_jobs`, `model_publications`; `recognition_models.owner_project_id/institution_id`
  (institutional scope), `model_versions.derived_from_id/training_job_id/provenance`; `model_source`
  gains `trained`. Permission `train_models` (PI, researcher).
* Institutional models are filtered from `/models` for non-members and refused by job submission
  outside their scope.
* `app.api.interop`: annotation management, W3C Web Annotations, analytics (confusions, CER by line
  length, reliability diagram and ECE), IIIF Presentation 3.0 manifests validated with the official
  validator's JSON Schema. Training-data export reuses the batch-export machinery (`scope=training-data`).

## 11. Phase 7 additions — production readiness

* **Malware scanning** (`app/services/malware.py`): clamd `INSTREAM` client used by every upload path
  (documents, model files, ground truth) before storage; fail-closed; reported by `/api/health/ready`.
  New Compose service `clamav` (official image, freshclam).
* **HTTP hardening** in the API middleware (CSP, frame, COOP/CORP, no-store on auth, optional HSTS) and
  in `next.config.ts`; API documentation served from vendored assets (`app/static/apidocs`).
* **Storage policies and retention** (`app/services/retention.py`): `Institution.storage_policy`
  validated by `StoragePolicy`; enforcement in the upload endpoint; retention run as CLI, Celery beat
  task `maintenance.retention` and admin endpoint. Storage backends gained `iter_objects()`.
* **Invitations**: `POST /api/v1/admin/users` (platform or institution administrators) reusing the
  single-use password token.
* **Backup/restore** (`app/ops/backup.py`, `scripts/backup.sh`, `scripts/restore.sh`): pg_dump +
  objects + models with SHA256SUMS and a research-data fingerprint; verified restore.
* **Performance**: batched document listing, SQL-side dashboard aggregation, migration `7a1c3e9d5b20`
  (28 foreign-key indexes), schema-drift test; scripts in `scripts/perf/`.
* **Operations**: `python -m app.cli security-check | retention | backup | restore | verify-backup`,
  `GET /api/v1/admin/security-status`, optional Caddy TLS proxy (`deploy/Caddyfile`).
