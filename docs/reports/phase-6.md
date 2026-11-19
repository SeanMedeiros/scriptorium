# SCRIPTORIUM — Phase 6 Technical Report: Advanced Features

Date: 2026-11-18 · Status: **DELIVERED** · Schema revision: `f233e02e5f64`

## 1. Scope delivered

| Master-prompt item | Delivered |
|---|---|
| Fine-tuning workflows (§14) | **Real Kraken training**: fine-tuning of any validated model (or training from scratch) on the project's ground truth; dedicated `training` queue/worker and separate process; data preparation to PAGE XML with Kraken polygons; per-epoch validation metrics; early stopping / fixed epochs; deterministic seed; cancellation; stale-worker detection; out-of-memory diagnosis |
| Dataset splitting & leakage (§14, §11) | train → training, validation → checkpoint selection, **test never given to the training process**; automatic evaluation of the new model on the test split (Phase 5 methodology); frozen data manifest with SHA-256 of every generated file |
| Checkpoint management (§14) | Best k checkpoints kept (weights only), listed with epoch and validation accuracy; register any checkpoint as another version; delete to free space |
| Model versioning & export (§14) | Trained weights registered as versions of a project-scoped institutional model, validated by Kraken before use; provenance (base model DOI/licence/attribution, job, config, data); weights download |
| Institutional model registration (§5.3, §6) | Upload `.mlmodel` / `.safetensors`; Kraken validation on the models worker; wrong model types reported as `incompatible`; scoping: invisible and unusable outside the owner project |
| Zenodo publication (§14) | Draft → PI approval (recorded statement) → package: weights, htrmopo v1 model card (validated against htrmopo's schema), Zenodo deposit metadata (`isDerivedFrom` base DOI), provenance; CC-BY derivative licence check; DOI recorded after a person publishes. The platform never uploads by itself |
| Annotation management | Project-wide list with filters (kind, open/resolved, text), links to lines; export as **W3C Web Annotation** collection (JSON-LD, media-fragment selectors) |
| Advanced analytics | From stored line-aligned results: character confusions, missed/inserted characters, CER by line length, **reliability diagram** (model confidence vs measured accuracy) and expected calibration error |
| Extended interoperability | **IIIF Presentation 3.0** manifests with transcription annotations, validated against the official IIIF validator's JSON Schema; Kraken/eScriptorium-ready **training-data export** (PAGE XML, validated) |

## 2. Real results

* Fine-tuning TraPrInq V1, 1 epoch, lr 1e-4, seed 42 — train 40 lines (687_m0019), validation 10 lines
  (12193_m0197), test 18 lines (7926_m0052, never seen): **validation accuracy 95.57 %**, **test CER
  8.70 %** against 8.32 % for the unmodified TraPrInq on the same page. One epoch on 40 lines does not
  improve the model; the platform reports what it measures. Repeated runs give identical figures.
* Duration on 2 vCPU without GPU: ≈ 100–110 s per job (≈ 70 s of training per epoch over 50 lines).
* Peak memory of the training process: 2.46 GB → **2.08 GB** with weights-only checkpoints
  (checkpoint files 480 MB → 160 MB, same accuracy).
* Analytics on the E2E project: most frequent substitutions a→r, a→e, e→r; reliability diagram shows
  e.g. mean confidence 0.84 for lines measured at 46 % character accuracy (ECE 0.22 over both models):
  confidences are not calibrated, as the interface states.

## 3. Verification evidence (`docs/evidence/phase6/`)

| Check | Result | Evidence |
|---|---|---|
| Backend pytest (real PostgreSQL, Redis, Kraken, Zenodo) | **177 passed** (19 labelled fixture-only) | `test-run.log` |
| Real training tests: fine-tuning → registration → test evaluation → scoping → publication; request validation; real cancellation | 3 passed | `tests/test_training_real.py` |
| Phase 6 tests: upload + Kraken validation + scoping, annotations + W3C, IIIF (validator rejects broken manifests), analytics, training-data export | 5 passed | `tests/test_phase6.py` |
| Frontend type-check + Vitest | 24 passed | `test-run.log` |
| E2E Playwright + Chrome, whole platform incl. real fine-tuning on the training worker | **30 passed** (4 new Phase 6 scenarios) | `e2e-run.log`, `01–05*.png` |
| Persistence after PostgreSQL + Redis restart | PASS — training job and epochs, trained model version, approved publication, 3 evaluation results (CER identical), all earlier data | `persistence.txt` |
| Reproducibility | Fine-tuning through the UI (E2E) gave exactly the pytest figures: validation 0.9557, test CER 0.0870 | `persistence.txt`, `02-training-job.png` |

## 4. Defects found and fixed

1. Manifest files given to Kraken were treated as XML: Kraken's CLI expands them first → paths passed directly.
2. Epoch metrics read before Kraken logs them → emitted from `on_validation_end`.
3. Job log entries stored as JSON strings (double serialisation) → typed JSONB literal; found by the real cancellation test.
4. **`iiif-prezi3` accepted a canvas without dimensions** (test caught it) → validation against the
   official IIIF presentation validator's JSON Schema; the library was removed (it also downgraded Pillow).
5. **Training killed by the kernel (OOM) in the E2E environment** (browser, API and four workers in 8 GB) →
   weights-only checkpoints, memory estimate warning, explicit `out_of_memory` diagnosis, smaller model
   cache in the E2E environment.
6. Jobs list stopped refreshing before the automatic test evaluation finished; the institutional models
   card did not refresh when training completed → both refresh until the result is available (found by E2E).
7. "Add ground truth" dialog listed the previous document's pages while the new list loaded — a quick
   selection targeted the wrong page (refused by the server as a duplicate) → options bound to the
   selected document, dialog state reset (found by E2E).
8. IIIF validation header in plain text while the client expects JSON → JSON like the other exports.
9. A test thread left its database session "idle in transaction", blocking the next test's cleanup →
   session closed; the training service now always ends its transaction.
10. Zenodo returned HTTP 500 with `Retry-After` ≈ 60 s during several E2E runs → registry E2E waits
    extended (the client already retries).
11. Untranslated annotation kind ("palaeography") in the workspace → localised; unused test imports cleaned.

## 5. Acceptance criteria (cumulative)

A–R: Met (Phases 1–5), re-verified by the full suites. Phase 6 adds §14 training "launched and its
results persisted" — demonstrated by real tests and E2E.

## 6. Limitations (explicit)

* CPU training only in this environment; real projects need a GPU training host (compose service ready).
* Training resumes are not offered (weights-only checkpoints); a new job can start from a registered version.
* Segmentation (baseline) model training (`ketos segtrain`) is not implemented — recognition only.
* The publication package must be uploaded by a person; no Zenodo token is used by the platform.
* IIIF manifests use signed, expiring image URLs (no public IIIF Image API service).
* Docker images still unbuilt here (Docker Hub blocked).
