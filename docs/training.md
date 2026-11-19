# Model training and institutional models

## What is implemented

* **Fine-tuning** of any validated Kraken recognition model usable in the project (TraPrInq, CATMuS,
  institutional models), or **training from scratch** (Kraken's default VGSL architecture).
* Training runs on a **dedicated `training` queue and worker**, in a separate process
  (`python -m app.training.runner`), never on the recognition workers.
* It uses Kraken's own training API (the same classes `ketos train` uses, Kraken 7.1.1).

## Data

A training job uses one evaluation dataset (project → Evaluation):

| Split | Used for |
|---|---|
| `train` | training |
| `validation` | checkpoint selection and early stopping (Kraken `val_accuracy`) |
| `test` | **never shown to the training process**; optionally evaluated afterwards with the Phase 5 methodology |

The worker writes one PAGE XML file per ground-truth page: line polygons (stored, or computed by
Kraken from the baselines), baselines and the reference text in diplomatic form. Reference lines with
an illegible gap are excluded and counted. The data manifest records the ground-truth snapshot ids,
line and character counts, SHA-256 of every generated file and image, normalisation, seed and
software versions. It is stored with the job and the trained model.

## Configuration

`epochs`, stop condition (`fixed` or `early` with `lag`), learning rate, batch size, codec resize
(`union` default: keep the base alphabet and add new characters), Unicode normalisation (defaults to
the base model's documented form, e.g. NFD for TraPrInq/CATMuS), augmentation, seed (default 42,
deterministic), number of checkpoints kept, automatic test evaluation.

## Result

The best checkpoint (by validation accuracy) is converted to weights (`.safetensors`), stored in the
content-addressed model store and registered as a new version of the project's **institutional
model**. It becomes usable only after Kraken loads it (status `validated`). Provenance: base model
(DOI, checksum, licence), attribution note, training job, checkpoint, validation accuracy,
configuration and data manifest.

Other kept checkpoints can be registered as further versions ("Register as version") or deleted to
free disk space. Cancelling stops the process at the next batch; nothing is registered.

## Institutional models

* Visible and usable only in the owner project (and listed only to its members); jobs elsewhere refuse
  them (404/422).
* Upload: project → Training & models → Upload model (`.mlmodel` or `.safetensors`). Kraken loads
  the file before use; a file of the wrong type is marked `incompatible` with the reason.
* Weights download: project members with the export permission.

## Publication on Zenodo

1. Prepare publication (title, description, authors with affiliation/ORCID, licence, keywords). A
   derivative of a CC-BY model must keep a CC-BY licence (checked).
2. The PI approves, confirming every author agreed. The platform then builds a package: weights,
   `README.md` (htrmopo v1 model card, validated against htrmopo's JSON schema), `zenodo.json`
   (deposit metadata with `isDerivedFrom` the base model DOI), `PROVENANCE.json`.
3. A person publishes it (`ketos publish` or the Zenodo web interface) and records the DOI in the
   platform. **SCRIPTORIUM never uploads to Zenodo by itself.**

## Memory

Measured peak resident memory of the training process (Kraken 7.1.1, CPU, TraPrInq base, batch 1):
**≈ 2.1 GB** (≈ 13 × the weights size). Checkpoints keep weights only (≈ the model size, 160 MB for
TraPrInq) to limit the peak while saving. The worker logs a warning when available memory is below the
estimate, and a process killed by the kernel is reported as *out of memory* with advice — this happened
during development when the browser, API and four workers shared 8 GB with a training job.

## Performance (measured here, 2 vCPU, no GPU)

One epoch over 50 lines of TraPrInq fine-tuning ≈ 70–100 s; preparation and registration add ≈ 30 s.
A GPU worker (`training-worker` service) is strongly recommended for real projects.

## Measured example (real, 2026-11-18)

TraPrInq V1 fine-tuned 1 epoch (lr 1e-4, seed 42) on 40 lines (train) with 10 lines (validation):
validation accuracy 95.57 %; test page (18 lines, never seen) CER 8.70 % — versus 8.32 % for the
unmodified TraPrInq on the same page. One epoch on 40 lines does not improve the model; the
platform reports this as measured. Identical results were obtained on repeated runs (deterministic).
