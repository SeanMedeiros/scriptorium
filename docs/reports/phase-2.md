# SCRIPTORIUM — Phase 2 Technical Report: Kraken Engine

Date: 2026-11-17 · Status: **DELIVERED** · Schema revision: `b393c36fc0a7`

## 1. Scope delivered

| Master-prompt item | Delivered |
|---|---|
| Install Kraken | Kraken **7.1.1** (latest on PyPI), PyTorch 2.14 CPU |
| Processing service | `app.recognition` — preprocessing, segmentation, reading order, recognition, diagnostics, structured errors |
| Test actual image recognition | Real TraPrInq inference on a real 16th-century Inquisition page, in pytest and in the browser |
| Segmentation | Kraken baseline segmenter (bundled `blla`), regions + lines + reading order, stored with coordinates |
| Job management | Celery queue, statuses, progress, logs, cancel, retry, batches, quotas, heartbeats, crash recovery |
| Verify output persistence | Immutable outputs; restart and worker-crash verification |

Also delivered because the engine needs them: document ingestion (PNG, JPEG, TIFF incl.
multi-frame, multi-page PDF), the model tables with manual installation and validation,
and a read-only page viewer. The full transcription editor remains Phase 4; Zenodo
automation remains Phase 3.

## 2. Test material (real, citable)

* **Model** — TraPrInq V1, Weslley Oliveira, DOI 10.5281/zenodo.14543904, CC-BY-4.0, NFD.
  Downloaded from Zenodo; MD5 `b7f84ede75a0665c275346d94fdc1b95` matched the record.
* **Page** — Arquivo Nacional Torre do Tombo, Tribunal do Santo Ofício, Inquisição de
  Lisboa, proc. 7926, image m0052 (16th-century procedural hand), from the *TraPrInq
  validation set* (Zenodo record 13986218, CC-BY-4.0, MD5 `e220fef2…` matched).
  Two more pages of the same set were assembled into a real 3-page PDF for the PDF tests.

## 3. Architecture

```
API ── POST /recognition-jobs ──▶ Redis (queue "recognition") ──▶ Worker
                                                                 │ claim (atomic, QUEUED→PROCESSING)
                                                                 │ heartbeat thread (15 s)
                                                                 │ load page image (immutable asset)
                                                                 │ preprocessing → DERIVED asset (+ affine to parent)
                                                                 │ kraken SegmentationTaskModel (blla)
                                                                 │ kraken RecognitionTaskModel (e.g. TraPrInq)
                                                                 └ ONE transaction: segmentation, regions, lines,
                                                                   recognition, line texts, job=completed, audit
Beat (60 s) / worker start ─▶ recover_stale_jobs(): PROCESSING without heartbeat → re-queued (max 3 attempts)
```

* Models are cached per worker process (LRU, keyed by path + SHA-256; ~28 s cold load).
* The API image imports neither PyTorch nor Kraken (verified with an import blocker).
* Coordinates are stored in the pixel space of the image actually segmented; when
  preprocessing changes geometry (rotation/deskew) the derived asset records the affine
  transform back to the original (verified to sub-pixel accuracy).

## 4. Data model added

`image_assets` (immutable), `documents`, `pages`, `recognition_models`, `model_versions`,
`recognition_jobs`, `segmentations`, `text_regions`, `text_lines`, `recognitions`,
`line_recognitions`. Database triggers reject `UPDATE` on assets and all recognition
outputs. Provenance chain per line: text → line (coordinates) → segmentation (input
asset, segmentation model, config, software versions) → recognition (model version,
config, software, timing) → job (submitter, times, log) → page → document → original file
(SHA-256).

## 5. Verification evidence

| Check | Result | Evidence |
|---|---|---|
| Backend pytest — real PostgreSQL, Redis, Kraken, TraPrInq | **81 passed**, coverage 90 % | `docs/evidence/phase2/test-run.log` |
| Frontend type-check + Vitest | **15 passed** | same |
| E2E Playwright + Chrome, real worker, real HTR | **12 passed** (incl. 54 s HTR job) | same + screenshots |
| Persistence across PostgreSQL + Redis restart | **PASS** (counts, text MD5, image and model SHA-256) | `persistence.log` |
| Worker killed with SIGKILL during recognition | **PASS** — re-queued, completed on attempt 2, no partial output | `worker-crash-recovery.log` |
| API importable without ML stack | PASS | report §3 |
| Compose file | valid (`docker compose config`) | — |

Key behaviours under test: original bytes preserved and downloadable with SHA-256;
content-based type detection (extension spoofing rejected); decompression-bomb and size
limits; PDF pages rendered at 300 dpi with recorded derivation; EXIF orientation recorded;
signed, expiring image URLs; deterministic preprocessing; deskew recovers known angles
(±0.15°); real segmentation ≥ 15 lines with in-bounds geometry; real recognition with
per-character confidences; identical text on re-recognition (determinism); outputs
immutable; re-recognising an existing segmentation creates a new output without touching
the first; blank page → `no_lines_detected` error with no partial output; retry; cancel;
quotas; permissions; non-model or wrong-task files → `incompatible`; checksum mismatch
blocks installation; stale-job recovery and attempt limit.

## 6. Observed recognition behaviour (honest notes)

* On the test page the default segmenter produced 37–39 lines, including a few spurious
  short lines (e.g. "=" with mean model score 0.19) and empty lines around the signature
  and marginal strokes. These are real outputs and are shown as such.
* Page-level character edit rate against the Transkribus ground truth: **0.329**. This is
  a **sanity check, not a CER**: line segmentation and reading order differ from the
  ground truth. The source-reported figures for TraPrInq (94.87 % character accuracy)
  were measured on ground-truth lines. Proper CER/WER evaluation (recognition on
  ground-truth segmentation, documented normalisation) is Phase 5 scope.
* Model scores are labelled everywhere as uncalibrated model confidences, never as accuracy.
* CPU timing on 2 vCPU: segmentation ≈ 29–32 s, recognition ≈ 19–23 s per page.

## 7. Defects found and fixed in this phase

1. Kraken 7 `device` means device *count* (Lightning), not type → crash; fixed via `accelerator`.
2. Hand-written Otsu threshold was wrong on colour pages → replaced with scikit-image.
3. `DecompressionBombError` escaped as HTTP 500 → reported as `image_too_large`.
4. Derived asset had no id before flush → NOT NULL violation (caught by the job's error reporting) → ids assigned eagerly.
5. Ingestion design would have updated immutable asset rows → dimensions read from headers at upload.
6. Celery eager-mode leak between tests made later tests pass/fail spuriously → explicit restore.
7. **Phase 2 migration silently lacked three foreign keys** (Alembic drops `use_alter` FKs in cycles) → added in `b393c36fc0a7`.
8. **Jobs orphaned by a lost worker stayed "processing" forever** → heartbeats, stale-job recovery, attempt limit, beat schedule; verified with a real SIGKILL.

## 8. Acceptance criteria status (cumulative)

| | Criterion | Status |
|---|---|---|
| A | register / log in | Met (Phase 1) |
| B | create a project | Met (Phase 1) |
| C | upload a historical document | **Met** — API, UI, E2E with a real manuscript |
| D | extract pages from a PDF | **Met** — real 3-page PDF, 300 dpi, E2E |
| E | real Zenodo metadata via the registry | Phase 3 (Zenodo reachable; metadata used manually here) |
| F | download and validate TraPrInq | **Partially** — downloaded with verified MD5 and validated by Kraken; automatic download through the registry is Phase 3 |
| G | CATMuS Medieval | Phase 3 |
| H | installed models load in Kraken | **Met** for TraPrInq and blla |
| I | submit a real HTR job | **Met** |
| J | Kraken segments and recognises the uploaded document | **Met** |
| K | transcription persisted | **Met** |
| L | edit recognised text | Phase 4 |
| M | original recognition preserved | **Met** (DB-enforced immutability) |
| N | export | Phase 5 |
| O | model provenance recorded | **Met** for recognitions |
| P | genuine processing errors reported | **Met** |
| Q | restart without data loss | **Met** (incl. worker crash) |
| R | automated tests of essential workflows | **Met for Phases 1–2** |

## 9. Limitations (explicit)

* Docker images still not built here (Docker Hub blocked); Compose validated only.
* No GPU available: the GPU path (`SCRIPTORIUM_RECOGNITION_DEVICE=gpu`) is untested.
* No malware scanner is connected; the upload path has a marked integration point.
* Segmentation can be inspected (viewer) but not yet edited — Phase 4.
* Models can only be installed by an administrator through the CLI until Phase 3.
