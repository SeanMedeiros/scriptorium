# Evaluation methodology (`scriptorium-metrics/1`)

## Ground truth

* A ground-truth page is an **immutable snapshot**: a segmentation (lines with coordinates) and one
  reference text per line, in one dataset and exactly one split (`train`, `validation`, `test`).
* Sources: a transcription whose **every line is marked reviewed** (unreviewed lines are still model
  output — accepting them would compare a model with itself), or an imported ALTO (v2–v4) / PAGE
  XML file for that page image. Imported coordinates are scaled when the file describes a
  proportionally resized copy; other sizes are refused.
* When the reference was corrected from a model's output, the snapshot carries a note: results of
  that model may be favoured by post-correction bias.
* **Leakage control**: a page — or a byte-identical image uploaded again (same SHA-256) — cannot sit
  in two different splits of the project's datasets. The training split is never evaluated.
  Withdrawal keeps the snapshot (old results stay interpretable).

## Runs

An evaluation run recognises the ground-truth pages of one split **on the ground-truth
segmentation** (Kraken computes polygons when the file only has baselines). Hypothesis and
reference are therefore line-aligned, as with `ketos test`. Every run and result stores its
normalisation options and the methodology version.

## Metrics

1. Reference markup is rendered diplomatically (abbreviations as written, deletions kept,
   editorial additions dropped). Reference lines with an illegible gap are excluded by default.
2. Unicode normalisation (default NFC; NFD, NFKC, none). Optional: case folding, punctuation
   removal, combining-mark removal. Whitespace is always collapsed. Applied to both sides.
3. Errors = Levenshtein distance (unit costs) over code points (CER) or whitespace tokens (WER).
4. CER = errors / reference characters, WER = errors / reference words. Aggregates are
   micro-averages (sum of errors / sum of reference units).
5. Accuracy = 1 − rate; undefined when insertions make the rate exceed 1.
6. When segmentations differ (ad-hoc scoring), lines are joined in reading order and the result is
   labelled `page`-aligned; reading-order differences then count as errors.

Model confidence values are never used in, or presented as, accuracy.

## Demonstration (real data, 2026-11-18)

Page PT/TT/TSO-IL/28/7926 m0052 (Torre do Tombo), Transkribus ground truth from the TraPrInq
`Validation_Set_FINAL` (Zenodo 13986218), 18 lines, 517 reference characters, NFC:

| Model | CER | WER |
|---|---|---|
| TraPrInq V1 (Zenodo 14543904) | 8.32 % (43/517) | 28.21 % (33/117) |
| CATMuS Medieval 1.5.0 (Zenodo 12743230) | 70.99 % | — |

One page is not a benchmark; and the model record does not state whether these validation pages were
excluded from training. The CATMuS figure illustrates a model applied outside its documented scope
(medieval manuscripts), not a defect of the model.
