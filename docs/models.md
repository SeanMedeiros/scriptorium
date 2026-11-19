# Recognition models — administrator guide

Status: Phase 3. Models are installed from Zenodo through the web interface or the REST API,
without changing application code. Manual registration of a local file remains available
(command line) for models that are not on Zenodo.

## Principles

* **Nothing is installed silently.** An administrator previews the record (title, authors,
  licence, files and checksums, all versions, model card, compatibility pre-check) and must
  explicitly accept the licence. Newer Zenodo versions are *reported* ("update available"),
  never installed automatically, and a model's active version changes only by explicit action.
* **Verified files only.** Every download is checked against the size and checksum published in
  the Zenodo record; a mismatch fails the installation. Files are stored read-only under
  `SCRIPTORIUM_MODEL_DIR/<sha256>/<file>`; the SHA-256 is re-checked before every load.
* **Validated means loaded.** Status `validated` is set only after the installed Kraken
  (7.1.1 here) loaded the file and it declared the expected task (recognition/segmentation).
* **No invented metadata.** Facts come from the Zenodo record or the model card published in it
  (htrmopo v0 `metadata.json` / v1 `README.md`, validated against htrmopo's JSON schemas).
  Accuracy figures are shown as *reported by the authors*. Attributes entered by administrators
  (languages, period, script, convention) are stored separately as **curated**, with a basis.
* **No redistribution.** Model files are used for local inference only; the platform offers no
  endpoint to download them.
* **No shared assumptions across a family.** Every CATMuS record is resolved, pre-checked,
  downloaded and validated independently.

## Statuses

| Status | Meaning |
|---|---|
| available | shown in preview/discovery, not installed |
| downloading | transfer in progress (bytes shown) |
| installing | verified file being placed in the model store |
| installed | stored; Kraken validation pending |
| validated | loaded by Kraken; usable for recognition |
| incompatible | Kraken cannot load it, or it is not the expected kind of model |
| failed | network, size or checksum error, or an interrupted installation (retryable) |
| update available | (model level) Zenodo has a newer version with a *different* model file |

## Installing a model from Zenodo (web interface)

1. **Models → Install from Zenodo.** Enter a DOI (`10.5281/zenodo.14543904`), a concept DOI
   (`10.5281/zenodo.10066218` resolves to the latest version), a record URL or a record id —
   or pick an entry of the recommended catalogue (TraPrInq, CATMuS Medieval).
2. **Preview** and read: authors, licence, files with checksums, versions (latest marked;
   versions publishing an identical binary are flagged), model card validity, accuracy
   reported by the authors, Unicode normalisation stated in the description (with the quote),
   compatibility pre-check.
3. Tick the licence confirmation and **Download and install**. Progress and the installation
   log are shown on the model card.
4. The first validated version of a model becomes its **active** version. Install other
   versions at any time; use **Activate** to pin a version or roll back.
5. **Check for updates** compares with Zenodo (also run daily by the scheduler). A newer
   record that republishes the same file (e.g. CATMuS Medieval 1.6.2 = 1.6.0 binary) is
   reported as a metadata-only revision, not as an update.
6. **Edit curated attributes** to describe what the source does not declare (cite the basis).
   These feed the model recommendation shown to researchers before transcription.

**Discover on Zenodo** searches records (e.g. `CATMuS`) and pre-checks each one; records
without a Kraken model file (datasets, papers, YOLO weights…) are marked incompatible.

## Adding another Kraken model from Zenodo — no code change

Any Kraken-compatible model published on Zenodo can be added at run time; nothing in the source code
lists the available models (the TraPrInq and CATMuS entries of the catalogue are suggestions only).

1. Find the record: *Models → Discover on Zenodo* (search, e.g. `CATMuS Print` or `ketos`), or take its
   DOI from the publication.
2. *Install from Zenodo* → paste the DOI / record URL → **Preview**. Check that the record is
   *Compatible* (a `.mlmodel` or `.safetensors` Kraken file is present), read the licence, the authors'
   reported accuracy and the transcription conventions (Unicode normalisation, abbreviations).
3. Confirm the licence → **Download and install**. The models worker downloads the file, verifies the
   checksum Zenodo publishes, stores it under `SCRIPTORIUM_MODEL_DIR/<sha256>/`, records licence and
   provenance, and has Kraken load it. Only a *Validated* version can be used for recognition.
4. Optionally *Edit curated attributes* (languages, period, script, document type, with the basis
   for each claim) so the recommendation panel can suggest the model to researchers.
5. *Activate* the version researchers should use by default (others remain selectable and can be
   re-activated to roll back).

Models not on Zenodo: platform administrators use `python -m app.cli register-model` (below);
project members with the *train models* permission can upload a model file to their project
(*Training & models → Upload model*), where it is scanned for malware, checksummed and validated by
Kraken before use, and remains private to that project.

## Same operations through the REST API

```bash
TOKEN=…  # access token of a platform administrator
curl -H "Authorization: Bearer $TOKEN" "$API/api/v1/admin/model-registry/preview?identifier=10.5281/zenodo.10066218"
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"identifier":"10.5281/zenodo.10066218","accept_license":true}' "$API/api/v1/admin/model-registry/install"
curl -H "Authorization: Bearer $TOKEN" "$API/api/v1/model-registry/models"
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"version_id":"<uuid>"}' "$API/api/v1/admin/models/<model-uuid>/active-version"
```

Full schema: `/api/docs` (tag *model registry*).

## Manual registration (models not on Zenodo)

```bash
cd backend
python -m app.cli register-model --file /path/model.mlmodel --slug my-model --name "My model" \
  --task recognition --version 1.0 --license cc-by-4.0 --checksum md5:<published md5> --normalization NFD
python -m app.cli register-bundled      # Kraken's bundled baseline segmenter (done by the worker at start)
python -m app.cli list-models
```

## Network requirements

The API (preview, discovery) and the `models` worker queue (downloads) need HTTPS access to
`zenodo.org`. Anonymous Zenodo API pages are limited to 25 items and are rate limited; set
`SCRIPTORIUM_ZENODO_TOKEN` to raise the limits. `SCRIPTORIUM_ZENODO_API_URL` allows an
institutional mirror.

## Verified in the development environment (2026-11-17)

| Model | Record | Licence | File checksum (Zenodo) | Result |
|---|---|---|---|---|
| TraPrInq V1 | 10.5281/zenodo.14543904 | CC-BY-4.0 | md5:b7f84ede…fc1b95 | downloaded, verified, validated (Kraken 7.1.1) |
| CATMuS Medieval 1.6.2 | 10.5281/zenodo.21488839 (latest of concept 10066218) | CC-BY-4.0 | md5:326a6ea6…e495 | validated; same binary as 1.6.0 |
| CATMuS Medieval 1.6.0 | 10.5281/zenodo.15030337 | CC-BY-4.0 | md5:326a6ea6…e495 | validated; flagged identical binary |
| CATMuS Medieval 1.5.0 | 10.5281/zenodo.12743230 | CC-BY-4.0 | md5:02bb2e4e…dc5a4 | validated; used for rollback tests |
