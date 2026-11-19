# SCRIPTORIUM — Phase 4 Technical Report: Document Workspace

Date: 2026-11-18 · Status: **DELIVERED** · Schema revision: `8372a9fc00d8`

## 1. Scope delivered

| Master-prompt item | Delivered |
|---|---|
| Upload, projects, PDF extraction, image viewer | Already in Phases 1–2; reused |
| Synchronised editor (§9) | Two-panel workspace: image (zoom, pan, fit width, polygons) ↔ lines; selection is synchronised in both directions; keyboard navigation |
| Human correction | Inline editing with autosave (≈1.5 s idle); undo/redo grouped by typing pause; save state always visible |
| Revision history | Numbered immutable revisions; read-only view of any past revision; character diff against HTR output or current text; per-line history and restore (as a new revision) |
| Distinct layers (§9) | Original HTR (immutable) · corrected text · normalised text · expanded abbreviations (derived view) · notes and comments |
| Palaeographic conventions (§10) | Editorial markup v1 (uncertain, illegible with extent, addition, deletion, abbreviation\|expansion, superscript), validated client- and server-side; configurable, *enforced* transcription policies; explicit Unicode form for typed text; no automatic expansion or modernisation |
| Review workflow | Line status unreviewed → corrected → reviewed; review requires the `review` permission |
| Segmentation adjustment (§4) | Delete lines, draw new baselines; Kraken computes polygons; derived segmentation; real re-recognition job; previous outputs kept |
| Collaboration | Optimistic concurrency, explicit conflict banner (mine/theirs); comments with resolve |
| Dashboard (§16) | Line counts per status from real records |

## 2. Data model added

`transcriptions` (one per recognition, policy snapshot), `transcription_revisions`
(number, kind, author, message, policy), `line_versions` (text, normalised text, note,
status, uncertain/illegible flags; only lines changed in that revision), `annotations`;
`projects.transcription_policy`. Revisions and line versions are protected by the
immutability trigger (evidence: `immutability.txt` — `UPDATE` rejected by PostgreSQL).

## 3. Verification evidence (`docs/evidence/phase4/`)

| Check | Result | Evidence |
|---|---|---|
| Backend pytest (real PostgreSQL, Redis, Kraken, Zenodo) | **140 passed** (of which 19 labelled fixture-only) | `test-run.log` |
| Real adjusted-segmentation test (manual baselines → Kraken polygons → TraPrInq) | passed | same |
| Frontend type-check + Vitest (incl. TS markup parser parity) | **24 passed** | same |
| E2E Playwright + Chrome, whole platform | **22 passed** (8 new workspace scenarios) | `e2e-run.log`, `01–07*.png` |
| Persistence after PostgreSQL + Redis restart | PASS — 9 revisions, 47 line versions, identical content MD5; HTR output MD5 unchanged | `persistence.txt` |
| Revision chain in database | initial (39 lines from TraPrInq V1) + edits + restore | `revision-chain.txt` |

E2E scenarios on a real Torre do Tombo page recognised with TraPrInq: synchronised
selection; correction with markup, autosave, undo/redo; invalid markup rejected and not saved;
abbreviation expansion and comparison with HTR output; past revision view and line restore;
comments; two sessions editing the same line → explicit conflict; layout adjustment with real
re-recognition; persistence and dashboard.

## 4. Defects found and fixed

1. Line versions inserted before their revision (FK violation) → flush order fixed.
2. `NameError` in the audit details of transcription creation (introduced by my edit) → fixed; found by the full suite.
3. Dashboard test still expecting the Phase 1 placeholder → updated, new line-status test.
4. Interface strings still announcing "arrives in Phase 4" (EN/PT/FR) → corrected.
5. Double-click when drawing a baseline added duplicate points → deduplicated and clamped to the page.
6. **One worker served the `models` queue**: a 160 MB model download blocked ingestion of uploads
   for minutes → dedicated `models` worker in dev script, E2E and Docker Compose.
7. **Undo did not revert after an autosave**: the undo stack compared snapshots by object identity
   and the typing timer was never cleared, so the post-save state was pushed again → snapshots now
   compared by content, synchronous state mirror, post-save Unicode adoption rewrites the current
   step instead of adding one.
8. Save indicator stuck on "Unsaved changes" when edits returned the text to its saved form → fixed.
9. Tests compared NFC literals with text stored in NFD under the TraPrInq policy → tests now state
   the normalisation explicitly (behaviour was correct; the expectation was not).
10. SQLAlchemy deprecation (`distinct(expr)`) → `postgresql.distinct_on`.

## 5. Acceptance criteria status (cumulative)

| | Criterion | Status |
|---|---|---|
| A–K | account … persisted recognition | Met |
| L | researcher can edit recognised text | **Met** — workspace, API, E2E |
| M | original recognition preserved | **Met** — HTR rows immutable; MD5 unchanged after edits and restart |
| N | export | Phase 5 |
| O | model provenance | Met — each transcription → recognition → model version (Zenodo, checksum) |
| P | genuine errors reported | Met (incl. markup/policy 422 and conflict 409) |
| Q | restart without data loss | Met |
| R | automated tests | Met for Phases 1–4 |

## 6. Limitations (explicit)

* Markup is a SCRIPTORIUM inline notation; its TEI mapping is documented but TEI export (with
  schema validation) is Phase 5.
* Text regions cannot yet be drawn or edited by hand (lines/baselines only); region-level
  reading-order editing is not implemented.
* Real-time co-editing is not provided: concurrent edits are detected and resolved explicitly.
* Undo/redo is per browser session; across sessions, history and restore are used instead.
* Docker images still unbuilt in this environment (Docker Hub blocked); compose validated.
