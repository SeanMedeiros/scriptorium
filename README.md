# SCRIPTORIUM — Historical Document Intelligence Platform

Research platform for transcribing historical manuscripts and printed documents with
Kraken HTR/OCR, human correction, scientific provenance and standard exports
(ALTO, PAGE, TEI, hOCR).

> **Delivery status.** The platform is built in controlled phases (see
> `docs/reports/`). Only features listed as *verified* in the latest phase report
> are operational. Nothing in the interface simulates a feature that does not
> exist yet; pending modules are shown as such.

| Phase | Scope | Status |
|---|---|---|
| 1 Foundation | architecture, PostgreSQL schema, auth, RBAC, institutions, projects, collections, audit, storage, UI, Docker, tests | **Delivered** — `docs/reports/phase-1.md` |
| 2 Kraken engine | ingestion, preprocessing, segmentation, recognition, job queue, crash recovery | **Delivered** — `docs/reports/phase-2.md` |
| 3 Model registry | Zenodo, TraPrInq, CATMuS, checksums, versions, recommendation | **Delivered** — `docs/reports/phase-3.md` |
| 4 Document workspace | synchronised editor, human correction, editorial markup, policies, revisions, conflicts, layout adjustment | **Delivered** — `docs/reports/phase-4.md` |
| 5 Research tools | ground truth, CER/WER, model comparison, 8 export formats with schema validation, provenance, reports | **Delivered** — `docs/reports/phase-5.md` |
| 6 Advanced | real Kraken fine-tuning, institutional models, publication packages, annotation management, analytics, IIIF / W3C / training-data interoperability | **Delivered** — `docs/reports/phase-6.md` |
| 7 Production readiness | security review (malware scanning, headers, dependency audits), retention and storage policies, invitations, backup/restore, performance, deployment documentation, final acceptance | **Delivered** — `docs/reports/phase-7.md` |

## Stack

* **Backend** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL 16
* **Jobs** Celery · Redis 7 (queues `default`, `recognition`; `models` and `training` on dedicated workers)
* **Recognition** Kraken 7.1.1 (PyTorch) — worker image only
* **Storage** content-addressed, write-once; local filesystem or S3/MinIO
* **Frontend** Next.js 16 · React 19 · TypeScript · Tailwind 4 · Radix (shadcn/ui pattern) · EN/PT/FR
* **Tests** pytest (real PostgreSQL/Redis/Celery) · Vitest · Playwright (Chrome)

## Quick start (development, native services)

```bash
# prerequisites: Python 3.12, Node 22, PostgreSQL 16, Redis 7  (see docs/installation.md)
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e "backend[worker,test]"
(cd frontend && npm ci)
sudo -u postgres psql -c "CREATE ROLE scriptorium LOGIN PASSWORD 'scriptorium_dev' CREATEDB" \
                      -c "CREATE DATABASE scriptorium OWNER scriptorium" \
                      -c "CREATE DATABASE scriptorium_test OWNER scriptorium"
cp backend/.env.example backend/.env
scripts/dev-up.sh            # API :8000, worker (+scheduler), web :3000
# install recognition models from Zenodo: Models → Install from Zenodo (docs/models.md)
```

Open http://localhost:3000 and register: **the first account becomes the platform administrator.**
API documentation (OpenAPI): http://localhost:8000/api/docs

## Tests

```bash
scripts/test-all.sh          # backend + frontend + end-to-end
```

## Deployment (Docker Compose)

```bash
cp deploy/.env.example deploy/.env    # set every secret
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Includes ClamAV (uploads scanned before storage, fail-closed) and an optional Caddy TLS proxy
(`--profile tls`). See `docs/deployment.md`, `docs/admin-guide.md` and `docs/backup-recovery.md`.

## Documentation

* `docs/architecture.md` — architecture and decisions
* `docs/installation.md` — local development installation
* `docs/deployment.md` — production deployment (Docker Compose, TLS, sizing, checklist, operations)
* `docs/admin-guide.md` — accounts and invitations, roles, storage policies, retention, audit
* `docs/backup-recovery.md` — backup sets, verified restore, recovery scenarios
* `docs/troubleshooting.md` — error codes and fixes
* `docs/api.md` — REST API conventions and endpoint index (`docs/api/openapi.json`)
* `docs/database.md` — schema, provenance chain, integrity rules, table catalogue
* `docs/performance.md` — recognition throughput, API load test, database review
* `docs/security.md` — authentication, authorization, audit, data protection
* `docs/models.md` — installing recognition models
* `docs/researcher-guide.md` — transcription workspace, editorial markup, policies, evaluation, exports
* `docs/evaluation.md` — ground truth and CER/WER methodology
* `docs/tei-mapping.md` — TEI export mapping
* `docs/training.md` — fine-tuning, institutional models, publication
* `docs/interoperability.md` — IIIF, W3C annotations, training data
* `docs/reports/phase-N.md` — phase reports with evidence

## Privacy

Uploaded documents are never sent to third-party AI services. All recognition runs on
the institution-controlled Kraken workers.
