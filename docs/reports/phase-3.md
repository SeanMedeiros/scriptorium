# SCRIPTORIUM — Phase 3 Technical Report: Model Registry

Date: 2026-11-17 · Status: **DELIVERED** · Schema revision: `6f9c7603f2aa`

## 1. Scope delivered

| Master-prompt item | Delivered |
|---|---|
| Integrate Zenodo API | `app.registry.zenodo`: record, versions, latest, search, file download; retries with `Retry-After`; anonymous page limit (25) respected; optional token / mirror |
| Register TraPrInq | Installed from Zenodo through the UI (real 160 MB download), MD5-verified, validated |
| Register CATMuS Medieval | Concept DOI → latest (1.6.2); 1.6.0 and 1.5.0 also installed and validated |
| Discover CATMuS models | Zenodo search with independent per-record pre-checks |
| Downloads + checksum verification | Streaming download with progress; size and published checksum verified before installation |
| Version management | Active version per model, pinning, rollback, update check (daily + on demand), identical-binary detection |
| Validate actual model loading | Status `validated` only after Kraken 7.1.1 loads the file and it declares the expected task |

Also delivered (master prompt §6–7): licence display and mandatory acceptance (recorded with
user and time), model cards validated against htrmopo's schemas, source-reported accuracy
labelled as such, Unicode normalisation extracted only from an explicit statement in the
record (quote stored), administrator curation kept separate from source facts, and a
metadata-based recommendation shown in the "Run recognition" dialog with explicit evidence,
mismatches, unknowns and limitations.

## 2. Findings about the real records (verified, not assumed)

* **CATMuS Medieval's latest version is 1.6.2** (record 21488839, 2026-07-22), not 1.6.0. Its
  model file is **byte-identical** to 1.6.0 (same MD5 and size); 1.6.2 adds an htrmopo v1
  model card (README.md, YAML) declaring languages `lat, oci, frm, dum, fro, ita, osp`.
  The registry reports such a revision as metadata-only, not as an update.
* TraPrInq's card (htrmopo v0) declares `accuracy: 94.87` and script `Latn`, but **no
  languages**; Portuguese/16th–19th c. come from the record *title* and are stored as curated
  facts with that basis.
* CATMuS Medieval 1.5.0's card is schema-valid; its single author entry concatenates all 17
  names in BibTeX style. Shown verbatim. *(Correction: during development I first believed
  this card was malformed because my own inspection had truncated the field; the test
  revealed the error and the claim was removed.)*
* Records in the CATMuS search include datasets, a YOLO detector (`.bin` shards), papers and
  `.safetensors` models: each is pre-checked independently; non-Kraken records are marked
  incompatible.

## 3. Real recognition with registry-installed models

* **TraPrInq** (installed via UI) — Torre do Tombo Inquisition page, full job via worker (E2E).
* **CATMuS Medieval 1.6.2** (installed via registry) — 13th-c. Occitan bifolium BnF fr. 14771
  f. 6 (HTRomance *medieval-occitan* v0.0.1, Zenodo 22656364, CC-BY-4.0, MD5 verified;
  chosen because the card does not list it among the training corpora and it was published
  after the model binary). 104 lines, e.g. "En aquest an sera trops fruitz", "Aquesta uia er
  longa ⁊ ala finbona": graphematic output with abbreviation signs (⁊, ꝑ) and a MUFI
  private-use character preserved, as documented by the model.

## 4. Verification evidence

| Check | Result | Evidence |
|---|---|---|
| Backend pytest (real PostgreSQL, Redis, Kraken; real Zenodo downloads) | **108 passed** | `docs/evidence/phase3/test-run.log` |
| of which fixture-only (labelled, HTTP mock for corrupted/truncated downloads, 429) | 18 | `tests/test_registry_unit.py` |
| Frontend type-check + Vitest | 15 passed | same |
| E2E Playwright + Chrome (real Zenodo installs through UI, real HTR) | **14 passed** | same + `docs/evidence/phase3/*.png` |
| Persistence after PostgreSQL + Redis restart | PASS (4 validated models, active versions, licences, text MD5 identical to Phase 2 run) | `persistence.log` |
| API / registry importable without the ML stack | PASS | report §6 |

## 5. Defects found and fixed in this phase

1. Ambiguous ORM relationship after adding `active_version_id` (two FK paths) → explicit `foreign_keys`.
2. **`/models` returned HTTP 500** and the default-segmenter lookup was broken by the same
   ambiguity in two `join()` calls; backend registry tests did not cover those routes — found
   by E2E, fixed with explicit join conditions, regression test added. Practice adopted: the
   full suite is run after every schema change.
3. Registry UI stopped polling at the transient `installed` status (validation still running)
   → polling continues through `installed`, bounded to 20 minutes.
4. Unit test used an id rejected by the identifier parser.
5. Wrong premise about the CATMuS 1.5.0 card (see §2) — corrected in tests and documentation.
6. Very long model slugs → short title prefix (`traprinq`, `catmus-medieval`).

## 6. Data model added

`model_licenses`; on `recognition_models`: concept record, family, active version, source
languages/scripts, curated attributes, latest known version, update flag, last check; on
`model_versions`: publication date, download URL, bytes downloaded, licence acceptance (who,
when), model card (format, validity, errors, verbatim card), compatibility pre-check,
installer. Data migration populated licences and active versions of existing installs.

## 7. Acceptance criteria status (cumulative)

| | Criterion | Status |
|---|---|---|
| A–D | account, project, upload, PDF pages | Met |
| E | registry retrieves real Zenodo metadata | **Met** |
| F | download and validate TraPrInq | **Met** — through the registry UI, MD5 verified, Kraken-validated |
| G | download and validate CATMuS Medieval | **Met** — 1.6.2 (latest), 1.6.0, 1.5.0 |
| H | installed models load in Kraken | **Met** |
| I–K | real HTR job, segmentation + recognition, persisted | Met (TraPrInq and CATMuS) |
| L | edit recognised text | Phase 4 |
| M | original recognition preserved | Met |
| N | export | Phase 5 |
| O | model provenance | **Met** — recognition → model version → Zenodo record, checksum, licence acceptance |
| P | genuine errors reported | Met (jobs and installations) |
| Q | restart without data loss | Met |
| R | automated tests | Met for Phases 1–3 |

## 8. Limitations (explicit)

* Recommendation ranks by declared/curated attributes only; it cannot judge palaeographic
  suitability. Comparison on sample pages and CER/WER evaluation arrive in Phase 5.
* Licence acceptance is recorded but licences are not interpreted legally; SPDX URLs are
  linked only for Creative Commons ids.
* Zenodo anonymous rate limits apply; heavy discovery use should configure a token.
* Docker images still unbuilt in this environment (Docker Hub blocked).
