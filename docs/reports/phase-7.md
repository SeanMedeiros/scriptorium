# SCRIPTORIUM — Phase 7 Technical Report: Production Readiness and Final Delivery

Date: 2026-11-19 · Environment: Ubuntu 24.04, 2 vCPU, 7.8 GB RAM (6 GB memory cgroup for the test
stack), no GPU · Kraken 7.1.1, PyTorch 2.14 (CPU), PostgreSQL 16, Redis 7, ClamAV 1.5.4, Next.js 16.

Part A reports the Phase 7 work. Part B is the final deliverable required by §27 of the master
prompt (items 1–10 and the acceptance checklist A–R with evidence).

---

# Part A — Phase 7

## A1. Scope delivered

| Area | Delivered |
|---|---|
| Security review | Real **ClamAV malware scanning** of every upload path (documents, model files, ground truth) before storage, fail-closed, audited, reported by `/ready`; HTTP hardening (CSP, frame, COOP/CORP, no-store, optional HSTS) on API and web; self-hosted API docs (no CDN); production guard against wildcard CORS; dependency audits (pip-audit, npm audit), static analysis (bandit, ruff S/ASYNC) with every finding fixed or justified; **CVE-2026-58659 (lightning) remediated** |
| Data governance | Institutional **storage policies** (per-file limit, accepted types, originals quota, export retention, hosting statement) enforced at upload, with usage and UI; **retention run** (export expiry, unreferenced assets after deletion, orphan objects; grace period; dry run; audited) as CLI, daily scheduled task and admin endpoint |
| Accounts | **Invitations** (platform or institution administrators) for installations without self-registration — previously no way to create accounts once registration was closed |
| Backup & recovery | `backup.sh` / `restore.sh` (pg_dump + objects + models, SHA256SUMS, research-data fingerprint); restore verifies everything before writing, refuses non-empty targets, re-hashes every asset and model, compares the fingerprint |
| Performance | Recognition throughput benchmark, API load test, FK-index audit + migration (28 indexes), N+1 and data-transfer fixes, schema-drift test |
| Deployment | ClamAV and optional Caddy TLS proxy in Compose, loopback-bound web port, worker memory recycling, `security-check` CLI and `admin/security-status` |
| Documentation | deployment, administrator guide, backup/recovery, troubleshooting, API (+ OpenAPI snapshot), database architecture, performance, models guide (adding models without code changes), security review |

## A2. Verification evidence (`docs/evidence/phase7/`)

| Check | Result | Evidence |
|---|---|---|
| Backend pytest — real PostgreSQL, Redis, Celery, Kraken, Zenodo, **real clamd** | **189 passed, 0 failed, 0 skipped** (19 of them labelled `fixture_only`) | `test-run.log` |
| Phase 7 tests | 12 passed: headers/docs, HSTS, CORS guard, clamd detection (documents, model files, ground truth), fail-closed, storage policy, retention, admin endpoint, backup/restore round trip, invitations, migrations ↔ models | `tests/test_phase7.py`, `tests/test_migrations.py` |
| Frontend type-check + Vitest | 24 passed; production build OK | `vitest.log` |
| **E2E Playwright + Chrome, whole platform, real stack incl. real clamd** | **34 passed** (4 new Phase 7 scenarios) | `e2e-run.log`, `01–03*.png` |
| Persistence after PostgreSQL + Redis restart | PASS — every count and content hash identical, 10 images and 5 model files re-verified | `persistence.txt` |
| Backup → tamper test → restore into a new DB → API serves restored data | PASS — fingerprint identical; a single flipped byte rejected; non-empty target refused; 6 page images served and re-hashed | `backup-run.json`, `restore-run.txt`, `restored-api-check.txt` |
| Dependency audit | Python: 1 vulnerable package (lightning) → fixed; npm: 0 | `pip-audit.json`, `npm-audit.json` |
| Static analysis | bandit: 0 open issues (≈10 k LOC); ruff S/ASYNC: only justified items | `bandit.txt`, `ruff-security.txt` |
| Performance | see A3 | `load-test-*.json`, `query-bench-*.json`, `recognition-bench-traprinq.txt`, `fk-audit-*.txt` |

## A3. Measured performance (details: `docs/performance.md`)

* **Recognition (CPU, 2 threads)**: segmentation 23.3 s + TraPrInq recognition 13.0 s per page →
  **≈ 99 pages/hour per worker**; re-recognition on an existing segmentation ≈ 5–9 s (CATMuS) in E2E.
* **API** (2 uvicorn processes, 10 k-document collection with labelled synthetic volume):
  5 users → 36.5 req/s, p50 83 ms, p95 311 ms, 0 errors; 20 users → 37.7 req/s (CPU-bound),
  p95 0.91 s, 0 errors. Before the fixes, 20 users gave 14.9 req/s and p95 4.6 s.
* Document listing 138 → 26 ms; dashboard 347 → 96 ms (single request).
* All hot queries < 25 ms at 10 k documents / 50 k assets / 200 k audit records.

## A4. Defects found and fixed in Phase 7

1. **No malware scanning existed** (only a code comment) → real clamd integration, fail-closed.
2. **lightning 2.6.1 (CVE-2026-58659)** pinned by kraken 7.1.1 → 2.6.6 installed over the pin; real
   recognition and fine-tuning suites re-run green with it.
3. **No way to create accounts once self-registration is disabled** (the production default) → invitations.
4. Institution `storage_policy` column existed but was never used → validated, enforced, managed in the UI.
5. Deleted documents' files were never removed (rows and objects stayed forever) → retention run.
6. API documentation loaded JavaScript and fonts from public CDNs (data-sovereignty breach) → vendored.
7. Document listing issued 2 queries per document; dashboard shipped every visible page id from Python
   to SQL → batched queries / SQL joins (5× and 3.6× faster).
8. 57 unindexed foreign keys (28 relevant) → migration `7a1c3e9d5b20`; drift test added.
9. Blocking file and socket I/O inside async handlers (model upload, ground-truth import) → threadpool.
10. Export ZIP spool files and the training log handle were not closed deterministically → closed.
11. `assert`-based control flow and silent `except: pass` → explicit errors / logged diagnostics.
12. E2E: the web CSP overwrote the API's stricter CSP on proxied `/api/*` responses → web headers scoped
    to pages (found by the new E2E test).
13. E2E environment: Chrome and the training process killed by the 6 GB memory cgroup → recognition
    worker recycled above a memory threshold (`--max-memory-per-child`, also in the Compose file),
    E2E model cache 1.
14. Test robustness: Swagger UI renders tag groups progressively → the contract is checked on the spec;
    a document-count assertion raced the table load → anchored on a known row.

## A5. Limitations (explicit, Phase 7)

* **Docker images were not built here** (Docker Hub blocked). Compose is validated and every service
  command runs natively in the tests; build and smoke-test the images in your infrastructure
  (`docs/deployment.md` §6).
* ClamAV's **official signature mirror was unreachable** from this environment: the real daemon ran with
  a one-line EICAR test signature (plus any official databases present). Production uses freshclam.
* The web CSP must allow inline scripts (Next.js App Router without nonces).
* The 20-user load test ran on the same 2 vCPUs as the API; numbers are a floor, not a capacity plan.
* Redis data is not part of backups; jobs still *Queued* when Redis data is lost must be resubmitted.

---

# Part B — Final deliverable (§27)

## 1. Architecture summary

```
Browser ── Next.js 16 web (EN/PT/FR, same-origin /api proxy, CSP)
              │
          FastAPI API (auth, RBAC, audit, uploads → clamd, signed asset URLs, OpenAPI)
              │                          │                      │
        PostgreSQL 16              Redis 7 (Celery)        Object storage (local / S3-MinIO,
   (35 tables, immutable           queues: default,         content-addressed, write-once)
    research records,              recognition, models,
    append-only audit)             training
              │
   Workers: recognition (Kraken 7.1.1 segmentation + recognition) · models (Zenodo download,
   checksum, Kraken validation) · training (Kraken fine-tuning subprocess) · scheduler (recovery,
   update checks, retention) · ClamAV daemon
```

Frontend, API, recognition engine (`app/recognition`), model registry (`app/registry`), storage
(`app/storage`), evaluation, exports and training are separate modules with explicit interfaces.
Full description: `docs/architecture.md`, `docs/database.md`.

## 2. Implemented features

Authentication (Argon2id, rotating refresh tokens with theft detection, lockout, reset, invitations),
seven roles with institution isolation, audit trail · projects, collections, documents with Dublin
Core metadata, multi-file and multi-page PDF/TIFF ingestion, originals preserved byte-for-byte ·
reproducible preprocessing with recorded derivations · Kraken segmentation and recognition jobs with
progress, cancellation, retry, crash recovery · Zenodo model registry (preview, licence, checksum,
validation, versions, pinning, rollback, update checks, discovery, recommendation) with TraPrInq and
CATMuS Medieval · synchronised two-panel transcription workspace with editorial markup, policies,
Unicode handling, revisions, history, restore, conflicts, annotations, layout correction ·
ground-truth datasets with leakage control, CER/WER by documented methodology, model comparison and
combination · 8 export formats with schema validation, batch exports, provenance, reports · Kraken
fine-tuning, institutional models, publication packages · IIIF, W3C annotations, training-data export,
analytics · malware scanning, storage policies, retention, backups, performance tooling, deployment.

## 3. Working features verified by tests

189 backend tests (real PostgreSQL, Redis, Celery, Kraken, Zenodo, ClamAV; 19 isolated tests use
explicitly labelled fixtures and are not counted as HTR integration), 24 frontend tests, 34 end-to-end
scenarios driving Chrome through the real stack. The 34 E2E scenarios cover: registration and empty
dashboard; institution/project/collection; session and logout; localisation; isolation; password reset;
audit; TraPrInq and CATMuS installation from Zenodo with rollback; discovery; real HTR display and
synchronisation; correction with markup, autosave, undo/redo; abbreviations and views; history and
restore; comments; concurrent-edit conflict; layout adjustment with re-recognition; persistence of
corrections; upload of an image and a multi-page PDF; real HTR job; batch cancel; dashboard; ground
truth and real evaluation; model comparison; validated exports and provenance; batch export and report;
training data; real fine-tuning; publication; annotations/analytics/IIIF; security headers and
self-hosted docs; storage policy; invitation; infected upload refused.

## 4. Features not completed (explicit)

* Docker images not built in this environment (see A5); GPU execution untested (no GPU here).
* Segmentation-model training (`ketos segtrain`) — only recognition fine-tuning is implemented.
* Training cannot resume from a checkpoint (a new job can start from any registered version).
* The platform does not upload to Zenodo itself; publication packages are deposited by a person.
* IIIF Image API service not provided (manifests reference signed, expiring image URLs).
* Web CSP allows inline scripts; ClamAV verified with a test signature only (see A5).
* "Character/word accuracy" is shown only where defined (≤ 100 % error); model confidences are
  displayed as uncalibrated, with the measured calibration error.

## 5. Installation instructions

`docs/installation.md` (native: Python 3.12 venv, Node 22, PostgreSQL 16, Redis 7; one command
`scripts/dev-up.sh`). Remember the security override `uv pip install --no-deps "lightning==2.6.6"`.

## 6. Local execution

```bash
scripts/dev-up.sh                 # migrations, API :8000, workers, scheduler, web :3000
open http://localhost:3000        # first account = platform administrator
scripts/clamd-test.sh && export SCRIPTORIUM_MALWARE_SCANNER=clamd   # optional local scanning
```

## 7. Production deployment

`docs/deployment.md`: `cp deploy/.env.example deploy/.env` (all secrets), `docker compose … build`,
`docker compose … --profile tls up -d` (Caddy TLS) or behind the institutional proxy (nginx example
given), then the pre-production checklist (`security-check`, `/ready`, headers, backup + restore test,
EICAR upload refused, one end-to-end transcription). Administration: `docs/admin-guide.md`;
backups: `docs/backup-recovery.md`; incidents: `docs/troubleshooting.md`.

## 8. Model installation

*Models → Install from Zenodo* → DOI/record → review metadata, licence, checksums, reported accuracy and
conventions → accept licence → download, verification, Kraken validation → *Activate*. Any Kraken model
on Zenodo can be added this way **without code changes**; non-Zenodo models via
`python -m app.cli register-model` or project upload. Verified: TraPrInq V1
(10.5281/zenodo.14543904, md5 b7f84ede…), CATMuS Medieval 1.6.2 / 1.6.0 / 1.5.0. Guide: `docs/models.md`.

## 9. Test execution

```bash
scripts/test-all.sh                     # backend + frontend + end-to-end
(cd backend && ../.venv/bin/pytest -q)  # 189 tests, ≈ 12.5 min on 2 vCPU (real Kraken and Zenodo)
(cd frontend && npx tsc --noEmit && npx vitest run)
scripts/e2e.sh                          # isolated stack on :8001/:3001, Chrome, ≈ 10 min
```

## 10. Real end-to-end transcription demonstration (E2E run of 2026-11-19, database `scriptorium_e2e`)

1. **Document** — page *PT/TT/TSO-IL/28/7926*, image `PT-TT-TSO-IL-28-7926_m0052.jpg` (Inquisition of
   Lisbon trial, TraPrInq validation set, Zenodo 13986218), JPEG 1370×1931, 388 299 bytes, SHA-256
   `637cc777874c9a4b…`, uploaded through the UI, scanned by clamd, stored byte-for-byte.
2. **Model** — TraPrInq V1 installed from Zenodo record 10.5281/zenodo.14543904 through the UI (licence
   CC-BY-4.0 accepted, published md5 verified, loaded by Kraken 7.1.1).
3. **Recognition** — job completed in 48.8 s on CPU (segmentation by Kraken's bundled blla model +
   recognition), 39 lines. First lines of the untouched HTR output (stored immutably):
   `gūtada ella /` · `=` · `yollo 2o arto / dise` · `eella ta q̄ a Ree andaua mt pobre` ·
   `nete vestida e nūca avira dt`.
4. **Human correction** — revision 1 copies the HTR output verbatim (39 line versions); the researcher's
   edits create revisions 2–9, e.g. line 3 `yollo 2o arto / dise` → `yollo 2o arto / dise {?dita?}`
   (uncertain reading marked with editorial markup); a line was restored from history (revision 7).
   The HTR output remains available unchanged next to the correction.
5. **Evaluation against researcher ground truth** (ALTO from the TraPrInq validation set, never the
   model's own output), 517 reference characters:

   | Model | CER | WER |
   |---|---|---|
   | TraPrInq V1 | **8.32 %** (43 errors) | 28.21 % |
   | TraPrInq fine-tuned in the platform (1 epoch, 40 lines) | 8.70 % (45) | 29.06 % |
   | CATMuS Medieval 1.6.2 (out of domain) | 60.15 % (311) | 101.71 % |

   (Zenodo reports 94.87 % character accuracy for TraPrInq on its own evaluation; 91.68 % measured here
   on one page — consistent, and reported as measured.)
6. **Export** — collection export in TEI, PAGE, ALTO, hOCR, TXT, PDF, DOCX: 4 XML files validated against
   the official schemas, 0 invalid; provenance records model, version, DOI, Kraken version, processing
   configuration and every revision.
7. **Persistence** — PostgreSQL and Redis restarted: all counts and content hashes identical
   (`persistence.txt`); backup → restore into a new database reproduces the same fingerprint.

## Final implementation checklist — acceptance criteria (§25)

| | Criterion | Status | Evidence |
|---|---|---|---|
| A | User can register and log in | **Met** | `test_auth.py` (17 tests); E2E 01 "first account…", "session survives…"; Phase 7 invitations (`test_invited_user_sets_password…`, E2E 07) |
| B | Researcher can create a project | **Met** | `test_projects_permissions.py::test_independent_researcher_project_lifecycle`; E2E 01 "admin creates an institution, a project and a collection" |
| C | Researcher can upload a historical document | **Met** | `test_ingest.py` (14 tests); E2E 03 "upload a manuscript image and a multi-page PDF"; uploads malware-scanned (`test_phase7.py`) |
| D | System extracts pages from a PDF | **Met** | `test_ingest.py::test_multi_page_pdf_is_rasterised_with_recorded_derivation`; E2E 03 (3-folio PDF → 3 pages) |
| E | Registry retrieves real Zenodo metadata | **Met** | `test_registry_zenodo.py::test_preview_traprinq_shows_real_metadata_without_installing`, `…catmus_concept_doi…`; E2E 02 |
| F | Download and validate TraPrInq | **Met** | E2E 02 "administrator previews and installs TraPrInq from Zenodo"; md5 b7f84ede… verified; status *validated* |
| G | Download and validate CATMuS Medieval | **Met** | E2E 02 "CATMuS Medieval: latest version via concept DOI, then an older version and rollback"; `test_install_versions_update_check_pinning_and_rollback` |
| H | Installed models load in Kraken | **Met** | validation loads each model (`kraken_version_validated` 7.1.1); `test_engine_segments_and_recognises_a_real_manuscript_page`; incompatible files detected |
| I | Researcher submits a real HTR job | **Met** | E2E 03 "real HTR job: segmentation + TraPrInq recognition by the worker"; `test_full_job_through_the_api_persists_traceable_outputs` |
| J | Kraken segments and recognises the document | **Met** | demonstration §10 (39 lines, 48.8 s); `recognition-bench-traprinq.txt`; CATMuS on a medieval page (`test_real_catmus_job…`) |
| K | Recognised transcription persisted | **Met** | `line_recognitions` rows, immutable trigger; `persistence.txt` (text MD5 identical after restart) |
| L | Researcher edits the recognised text | **Met** | `test_transcriptions.py` (13 tests); E2E 04 (8 scenarios) |
| M | Original recognition result preserved | **Met** | `test_edit_creates_revision_keeps_original_and_applies_unicode_policy`; DB triggers reject UPDATE on recognitions/line recognitions; HTR layer exportable (`test_tei_mapping…htr_layer_exports_raw_output`) |
| N | Researcher exports the transcription | **Met** | `test_every_format_exports_and_xml_validates_against_official_schemas`; E2E 05 exports (8 formats, schema-validated) |
| O | Platform records model provenance | **Met** | `test_provenance_and_report_are_complete_and_permissioned`; E2E 05 "provenance is complete"; trained models record their base model and data |
| P | System reports genuine processing errors | **Met** | `test_genuine_processing_error_is_reported`, `test_job_lost_too_many_times_fails_explicitly`, `test_checksum_mismatch_blocks_installation`, malware/scanner errors (Phase 7); structured error codes (`docs/troubleshooting.md`) |
| Q | Restart without losing persistent data | **Met** | `persistence.txt` (Phases 1–7, PostgreSQL + Redis restart); backup/restore round trip with identical fingerprint |
| R | Automated tests verify essential workflows | **Met** | 189 backend + 24 frontend + 34 E2E, all passing (`test-run.log`, `vitest.log`, `e2e-run.log`) |

All eighteen acceptance criteria are met and evidenced in this environment. The items in Part B §4 and
A5 are the remaining limitations; none is presented as operational.
