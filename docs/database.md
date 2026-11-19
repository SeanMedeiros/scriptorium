# Database architecture

PostgreSQL 16, schema managed exclusively by Alembic migrations (`backend/alembic/versions`, one
revision per phase plus the Phase 7 index migration). `tests/test_migrations.py` verifies that the
migrations and the SQLAlchemy models describe the same schema, and every test run builds the schema
from the migrations (never `create_all`), so migrations are exercised continuously.

## Entity hierarchy

```
Institution ─┬─ InstitutionMembership ── User ── UserSession
             └─ Project ─┬─ ProjectMembership
                         ├─ Collection ── Document ── Page ─┬─ Segmentation ── TextRegion ── TextLine
                         │                     │            │        └──────────────────────────┐
                         │               ImageAsset (original, page image, derivative; parent →) │
                         │                                  ├─ RecognitionJob → Recognition ── LineRecognition
                         │                                  └─ Transcription ── TranscriptionRevision ── LineVersion
                         │                                                    └─ Annotation
                         ├─ EvaluationDataset ── GroundTruthPage ── GroundTruthLine (+ GroundTruthWithdrawal)
                         │        └─ EvaluationRun ── EvaluationResult
                         ├─ Comparison, Export
                         └─ TrainingJob → ModelVersion (institutional) ── ModelPublication
RecognitionModel ── ModelVersion ── ModelLicense            AuditLog (append-only, no FKs to mutable rows)
```

Independent researchers create projects without an institution; everything below the project
works identically.

## Provenance chain

Every transcription line can be traced to: `LineVersion` (who, when, revision number, previous
version, origin line recognition) → `LineRecognition` (raw model text, confidences) → `Recognition`
(model version, Kraken and software versions, processing configuration, input asset) →
`Segmentation` (segmenter model version, parameters, input asset) → `ImageAsset` (SHA-256, derivation
recipe, parent asset) → the uploaded original (`Document.source_asset_id`). Ground truth snapshots
reference the exact lines and revision they were taken from; evaluation results reference the
recognition and model version they scored.

## Integrity rules enforced by the database

* **Immutable research records** — triggers reject `UPDATE` on image assets, segmentations, regions,
  lines, recognitions, line recognitions, transcription revisions, line versions, ground-truth
  pages/lines/withdrawals and evaluation results. Corrections create new rows; nothing is rewritten.
* **Append-only audit trail** — `audit_logs` rejects both `UPDATE` and `DELETE`, and has no foreign
  keys to mutable entities (deleting a project never rewrites history).
* `RESTRICT` foreign keys protect originals and model versions that are still referenced; `CASCADE`
  is used only for strictly owned children (project → collections → documents → pages …).
* Unique constraints: case-insensitive e-mail, institution slug, one ground-truth snapshot per page
  and dataset, page sequence per document, model version per model.
* Content-addressed storage keys (`namespace/ab/<sha256>`) and SHA-256 columns allow every stored
  file to be re-verified (`app.ops.backup.verify_restored`).

## Indexing

Primary keys are UUIDs (application-generated). Every foreign-key column used by joins, cascades,
history queries or retention checks is indexed (Phase 7 audit, `docs/performance.md`); listing
queries are covered by indexes on their filter columns. Columns referencing `users` are deliberately
unindexed (accounts are deactivated, not deleted).

## Table catalogue (generated from the live schema)

Schema revision `7a1c3e9d5b20` — 35 tables.

| Table | Columns | References (FK → table) | Indexes | Write rule |
|---|---|---|---|---|
| `annotations` | 10 | `line_id → text_lines`, `transcription_id → transcriptions` | 3 |  |
| `audit_logs` | 12 | — | 6 | no UPDATE/DELETE |
| `collections` | 9 | `project_id → projects` | 2 |  |
| `comparisons` | 8 | `page_id → pages`, `project_id → projects`, `segmentation_id → segmentations` | 5 |  |
| `documents` | 20 | `collection_id → collections`, `source_asset_id → image_assets` | 3 |  |
| `evaluation_datasets` | 7 | `project_id → projects` | 3 |  |
| `evaluation_results` | 18 | `gt_page_id → ground_truth_pages`, `model_version_id → model_versions`, `recognition_id → recognitions`, `run_id → evaluation_runs` | 6 | no UPDATE |
| `evaluation_runs` | 9 | `dataset_id → evaluation_datasets`, `model_version_id → model_versions` | 3 |  |
| `exports` | 15 | `project_id → projects` | 2 |  |
| `ground_truth_lines` | 5 | `gt_page_id → ground_truth_pages`, `line_id → text_lines` | 3 | no UPDATE |
| `ground_truth_pages` | 13 | `dataset_id → evaluation_datasets`, `page_id → pages`, `segmentation_id → segmentations` | 5 | no UPDATE |
| `ground_truth_withdrawals` | 5 | `gt_page_id → ground_truth_pages` | 2 | no UPDATE |
| `image_assets` | 14 | `parent_asset_id → image_assets` | 3 | no UPDATE |
| `institution_memberships` | 6 | `institution_id → institutions` | 3 |  |
| `institutions` | 9 | — | 2 |  |
| `line_recognitions` | 8 | `line_id → text_lines`, `recognition_id → recognitions` | 4 | no UPDATE |
| `line_versions` | 16 | `line_id → text_lines`, `origin_line_recognition_id → line_recognitions`, `previous_version_id → line_versions`, `restored_from_id → line_versions`, `revision_id → transcription_revisions`, `transcription_id → transcriptions` | 7 | no UPDATE |
| `model_licenses` | 5 | — | 1 |  |
| `model_publications` | 17 | `model_version_id → model_versions` | 2 |  |
| `model_versions` | 34 | `derived_from_id → model_versions`, `license_id → model_licenses`, `model_id → recognition_models`, `training_job_id → training_jobs` | 3 |  |
| `pages` | 8 | `document_id → documents`, `image_asset_id → image_assets` | 4 |  |
| `password_reset_tokens` | 6 | — | 3 |  |
| `project_memberships` | 6 | `project_id → projects` | 3 |  |
| `projects` | 14 | `institution_id → institutions` | 4 |  |
| `recognition_jobs` | 26 | `document_id → documents`, `input_segmentation_id → segmentations`, `output_recognition_id → recognitions`, `output_segmentation_id → segmentations`, `page_id → pages`, `project_id → projects`, `recognition_model_version_id → model_versions`, `retry_of_id → recognition_jobs`, `segmentation_model_version_id → model_versions` | 14 |  |
| `recognition_models` | 21 | `active_version_id → model_versions`, `owner_institution_id → institutions`, `owner_project_id → projects` | 5 |  |
| `recognitions` | 13 | `input_asset_id → image_assets`, `job_id → recognition_jobs`, `model_version_id → model_versions`, `page_id → pages`, `segmentation_id → segmentations` | 6 | no UPDATE |
| `segmentations` | 13 | `input_asset_id → image_assets`, `job_id → recognition_jobs`, `model_version_id → model_versions`, `page_id → pages`, `parent_segmentation_id → segmentations` | 6 | no UPDATE |
| `text_lines` | 9 | `region_id → text_regions`, `segmentation_id → segmentations` | 4 | no UPDATE |
| `text_regions` | 7 | `segmentation_id → segmentations` | 2 | no UPDATE |
| `training_jobs` | 27 | `base_model_version_id → model_versions`, `dataset_id → evaluation_datasets`, `evaluation_run_id → evaluation_runs`, `output_model_version_id → model_versions`, `project_id → projects` | 4 |  |
| `transcription_revisions` | 9 | `transcription_id → transcriptions` | 3 | no UPDATE |
| `transcriptions` | 12 | `base_recognition_id → recognitions`, `comparison_id → comparisons`, `page_id → pages`, `segmentation_id → segmentations` | 6 |  |
| `user_sessions` | 11 | `replaced_by_id → user_sessions` | 5 |  |
| `users` | 12 | — | 2 |  |

Foreign keys to `users` (authors, creators, approvers) are omitted from the reference column for
readability. Regenerate this catalogue from a migrated database with the query in the Phase 7 report
evidence, or inspect it with `\d+ <table>` in `psql`.
