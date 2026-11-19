# Researcher guide — transcription workspace

## Opening a transcription

Page → **Open in editor**. The first time, a transcription is created from the selected
recognition (revision 1 = the HTR output, unchanged). The left panel shows the image with line
polygons; the right panel the lines. Selecting a line highlights its polygon and vice versa.

Keyboard: `Enter`/`↓` next line · `Shift+Enter`/`↑` previous · `Ctrl+Z` undo · `Ctrl+Shift+Z` redo.
Changes are saved automatically ~1.5 s after you stop typing; each save is a numbered revision.

## Layers

| Layer | Where | Rule |
|---|---|---|
| Original HTR output | "Original HTR output" under the line | never modified |
| Corrected transcription | the line itself | your edits, with editorial markup |
| Normalised text | "Normalised text" field | optional editorial normalisation |
| Expanded abbreviations | *Expanded* view | derived from `{abbr|expansion}` only |
| Notes / comments | "Line note", Comments panel | free text, resolvable comments |

## Editorial markup (v1)

| Type | Meaning | TEI (export) |
|---|---|---|
| `{?text?}` | uncertain reading | `<unclear>` |
| `{illeg}` / `{illeg:5}` | illegible (≈ 5 characters) | `<gap reason="illegible">` |
| `{+text+}` | editorial addition | `<supplied>` |
| `{-text-}` | deleted in the source | `<del>` |
| `{q̃|que}` | abbreviation and your expansion | `<choice><abbr/><expan/></choice>` |
| `{^text^}` | superscript / interlinear | `<hi rend="superscript">` |

Literal braces: `\{ \}`. Select text and use the toolbar to wrap it. Invalid markup is
flagged in red and **not saved** until fixed.

## Policies

Each project has a transcription policy (type, Unicode form for typed text, abbreviations,
deletions, illegible extent, additions, uncertain readings…). The default follows the model's
documented conventions — e.g. TraPrInq/CATMuS text is NFD, so typed text is stored in NFD;
CATMuS is graphematic, so `{abbr|expansion}` is not allowed. Nothing is ever modernised or
expanded automatically.

## History

**History** lists revisions; any revision can be viewed read-only and compared with the current
text (character diff). **Line history** shows every version of one line; *Restore* creates a new
revision with the old text.

## Conflicts

If a colleague changed the same line since you opened it, the save is refused and a banner shows
both versions; choose **Use mine** or **Use theirs**.

## Layout

**Edit layout** lets you delete lines or draw a new baseline (click points, double-click to
finish) and re-run recognition. A new segmentation is created; the previous one is kept.

## Ground truth and evaluation (project → Evaluation)

1. Create a dataset. Add ground truth for a page: import its ALTO / PAGE XML, or choose a
   transcription whose every line is marked **reviewed**. Choose the split (test, validation, train).
2. **Evaluate models**: select installed models and the normalisation; each model recognises the
   page on the ground-truth segmentation, and CER/WER are computed per line and per page.
3. **Results** can be grouped by model, document, page, collection, language, historical period
   (from the document's recorded years), manuscript/print, document type, split or dataset.
   Methodology: `docs/evaluation.md`.

## Comparing models (page → Compare models)

Choose a segmentation (an imported ground-truth segmentation gives line-aligned CER/WER) and 2–6
models. The comparison shows processing time, each model's output with differences highlighted, and
the share of characters on which models disagree (not an accuracy). Pick the preferred output line by
line and **Create combined transcription**: every line remembers which model it came from.

## Exports

* From the editor: **Export** (current or displayed revision) in TEI, PAGE, ALTO, hOCR, TXT, JSON,
  DOCX or PDF, choosing the text layer. XML files are validated before download and the result is
  shown. TEI mapping: `docs/tei-mapping.md`.
* From the project: **Exports** tab, a whole document or collection as a ZIP with `manifest.json`
  (SHA-256 of each file, validation results, the text source of every page, skipped pages).
* **Provenance** (editor toolbar): source image checksums, segmentation, model DOI/checksum/licence,
  Kraken version, configuration, contributors, policy and review statistics.
* **Report** tab: counts, review progress, models used, contributors and evaluation results (CSV).

## Training a model (project → Training & models)

1. In **Evaluation**, build a dataset with `train`, `validation` and `test` ground-truth pages.
2. **New training job**: choose the dataset, the base model (e.g. TraPrInq) or a new model, a name,
   the stop condition, epochs, learning rate and seed. Start.
3. Follow the learning curve, checkpoints and log on the job page. When it finishes, the model is
   registered as an institutional model (usable only in this project) and evaluated on the test split.
4. Prepare a publication (draft) and, as PI, approve it to obtain a Zenodo-ready package.
   Details: `docs/training.md`.

## Annotations and analytics

* **Annotations** tab: every comment, query and palaeographic note of the project, filterable,
  exportable as W3C Web Annotations.
* **Evaluation → Error analysis**: most frequent character substitutions, CER by line length, and a
  reliability diagram comparing model confidence with measured accuracy.
* Document page → **IIIF manifest**: the pages with their transcription for IIIF viewers.
