# SCRIPTORIUM — Phase 5 Technical Report: Research Tools

Date: 2026-11-18 · Status: **DELIVERED** · Schema revision: `ab8a316ca42b`

## 1. Scope delivered

| Master-prompt item | Delivered |
|---|---|
| Ground-truth management (§11) | Evaluation datasets with train / validation / test splits; immutable ground-truth snapshots from a **fully reviewed** transcription or an imported ALTO (v2–v4) / PAGE XML file (coordinates verified and scaled); withdrawal recorded separately |
| Leakage control (§11) | A page — or a byte-identical image uploaded twice (same SHA-256) — cannot be in two splits of the project; the training split is never evaluated; post-correction bias noted automatically |
| CER / WER (§11) | Methodology `scriptorium-metrics/1` (`docs/evaluation.md`): diplomatic rendering of reference markup, explicit configurable normalisation (Unicode form, case, punctuation, combining marks, gap lines), Levenshtein over code points / tokens, micro-averages, accuracy undefined when rate > 1; stored with every result |
| Evaluation by dimension (§11) | Model, document, page, collection, language, historical period, manuscript/print, document type, split, dataset; mixed normalisations flagged; charts |
| Model benchmarking laboratory (§12) | 2–6 models on the same image **and** the same segmentation; processing time, Kraken version, outputs, character diffs, CER/WER when ground truth exists, disagreement between models (labelled as such); line-by-line selection → combined transcription with the origin of each line kept |
| Exports (§13) | TXT, JSON, ALTO 4.4, PAGE 2019, hOCR, TEI P5, DOCX, PDF; layers HTR / corrected / expanded / reading / normalised / markup; any past revision; configurable TEI mapping (`docs/tei-mapping.md`) |
| Schema validation (§13) | Offline against vendored official schemas: ALTO 4.4 XSD, PAGE 2019-07-15 XSD, TEI P5 4.12.0 `tei_all` RELAX NG; hOCR structural check (hOCR has no formal schema — reported as such) |
| Batch export (§13) | Document or collection → ZIP with `manifest.json` (SHA-256 per file, validation results, text source of each page, skipped pages); Celery task; write-once storage |
| Provenance (§19) | `/transcriptions/{id}/provenance` and dialog: image checksums, segmentation, model DOI / record / checksum / licence / authors, Kraken and torch versions, configuration, contributors, policy, review statistics, measured CER/WER, combined-from |
| Institutional reporting (§16) | Project report (JSON / CSV) and institution report; dashboard quality block (stored evaluation results only) |

## 2. Real demonstration

Ground truth: Transkribus ALTO of Torre do Tombo PT/TT/TSO-IL/28/7926 m0052 (TraPrInq
`Validation_Set_FINAL`, Zenodo 13986218, CC-BY-4.0) — 18 lines, 517 reference characters.
Recognition on the ground-truth baselines (Kraken polygons), line-aligned, NFC:

| Model (installed from Zenodo) | CER | WER | Time (2 CPU) |
|---|---|---|---|
| TraPrInq V1 (14543904) | **8.32 %** (43/517) | 28.21 % (33/117) | 13.3 s |
| CATMuS Medieval 1.5.0 (12743230) | 70.99 % | — | 3.1 s |

The E2E run reproduced the TraPrInq figure exactly through the interface (CER 8.32 %, WER 28.21 %)
and measured CATMuS Medieval **1.6.2** at 60.15 % CER on the same page (`02-comparison.png`).

Caveats stated in the platform and here: one page is not a benchmark; the model record does not say
whether this validation set was excluded from training; CATMuS is a medieval model applied outside
its scope (the result illustrates why the recommendation and comparison exist).

## 3. Verification evidence (`docs/evidence/phase5/`)

| Check | Result | Evidence |
|---|---|---|
| Backend pytest (real PostgreSQL, Redis, Kraken, Zenodo) | **169 passed** (19 labelled fixture-only) | `test-run.log` |
| New Phase 5 tests | 27 (metrics, real ALTO files, ground truth, leakage, immutability, permissions, 8 formats + schema validation, TEI mapping, batch ZIP, provenance, reports) + 2 real Kraken tests | `tests/test_research.py`, `tests/test_research_real.py` |
| Levenshtein cross-check | rapidfuzz = reference DP on 300 random strings | `test_levenshtein_matches_reference_implementation_on_random_strings` |
| Frontend type-check + Vitest | 24 passed | `test-run.log` |
| E2E Playwright + Chrome, whole platform (real Zenodo installs, real HTR) | **26 passed** (4 new Phase 5 scenarios) | `e2e-run.log`, `01–06*.png` |
| Persistence after PostgreSQL + Redis restart | PASS — ground truth (text MD5), 2 evaluation results (CER identical), comparison, combined transcription, export, HTR text MD5 | `persistence.txt` |

## 4. Defects found and fixed

1. Re-adding a withdrawn page to the same dataset raised an unhandled unique-constraint error (500)
   → explicit 409 `duplicate_ground_truth`.
2. Reference text identical in NFC and NFD was labelled "ASCII-compatible" (wrong for `q̄`) →
   "invariant".
3. TEI `application/@version` refused "V1" by `tei_all` → version only when numeric, label otherwise.
4. ALTO lines with empty text failed the XSD (String required) → empty String element.
5. PDF builder: cursor left at the right margin → explicit line breaks.
6. Field components without `htmlFor` (accessibility) → ids added.
7. Zenodo returned intermittent HTTP 500 during E2E → the client now retries 500 (idempotent GETs)
   with back-off; E2E waits for Zenodo `Retry-After`.
8. Wrong collection/field names in the first export draft (caught by the smoke test).
9. Phase 1 test and dashboard test expected the old "module pending" placeholder → updated.

## 5. Acceptance criteria (cumulative)

| | Criterion | Status |
|---|---|---|
| A–M | … | Met (Phases 1–4) |
| N | researcher can export the transcription | **Met** — 8 formats, batch, schema-validated XML |
| O | model provenance | **Met** — in every export and the provenance record |
| P | genuine errors reported | Met (incl. 409 leakage, 422 layer/format, failed exports with message) |
| Q | restart without data loss | Met (re-verified) |
| R | automated tests | Met for Phases 1–5 |

## 6. Limitations (explicit)

* TEI validation is against `tei_all`; no project ODD customisation is provided.
* ALTO words carry no individual coordinates (line geometry only); no word confidence (engine
  confidences are line-level and uncalibrated).
* Statistical significance of CER differences is not computed (no bootstrap intervals yet).
* Fine-tuning on the training split is Phase 6.
* Docker images still unbuilt here (Docker Hub blocked); compose validated, images now include DejaVu fonts.
