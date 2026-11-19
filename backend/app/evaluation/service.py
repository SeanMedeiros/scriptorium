"""Ground-truth datasets, evaluation runs and aggregation (master prompt §11)."""
from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections import defaultdict

from sqlalchemy import select

from app.core.errors import ApiError
from app.editorial import service as editorial
from app.evaluation import gt_import, metrics
from app.models import (
    Collection,
    Document,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    GroundTruthLine,
    GroundTruthPage,
    GroundTruthWithdrawal,
    ImageAsset,
    LineRecognition,
    LineStatus,
    ModelVersion,
    Page,
    Recognition,
    Segmentation,
    TextLine,
    TextRegion,
    Transcription,
)
from app.models.evaluation import SPLITS


def unicode_form(texts: list[str]) -> str | None:
    """Detect the normal form the reference text is in (informative; metrics normalise explicitly)."""
    joined = "".join(texts)
    if not joined:
        return None
    nfc, nfd = unicodedata.is_normalized("NFC", joined), unicodedata.is_normalized("NFD", joined)
    if nfc and nfd:
        return "invariant"  # identical in NFC and NFD (no composable character)
    return "NFC" if nfc else "NFD" if nfd else "mixed"


def active_pages(db, dataset: EvaluationDataset, split: str | None = None) -> list[GroundTruthPage]:
    withdrawn = select(GroundTruthWithdrawal.gt_page_id)
    q = select(GroundTruthPage).where(GroundTruthPage.dataset_id == dataset.id, GroundTruthPage.id.not_in(withdrawn))
    if split:
        q = q.where(GroundTruthPage.split == split)
    return list(db.scalars(q.order_by(GroundTruthPage.created_at)))


def _image_sha(db, page: Page) -> str:
    return db.get(ImageAsset, page.image_asset_id).sha256


def check_leakage(db, project_id: uuid.UUID, page: Page, split: str, dataset: EvaluationDataset) -> None:
    """A page — or a byte-identical image uploaded twice — may not sit in different splits of the
    project's datasets, and may not appear twice in the same dataset."""
    prior = db.scalar(select(GroundTruthPage.id).where(GroundTruthPage.dataset_id == dataset.id, GroundTruthPage.page_id == page.id))
    if prior:
        # Snapshots are never replaced, even after withdrawal (past results must stay interpretable).
        raise ApiError(409, "duplicate_ground_truth", "This page already has a ground-truth snapshot in this dataset "
                       "(possibly withdrawn); use another dataset for a new version", details={"gt_page_id": str(prior)})
    sha = _image_sha(db, page)
    rows = db.execute(
        select(GroundTruthPage, EvaluationDataset, Page)
        .join(EvaluationDataset, EvaluationDataset.id == GroundTruthPage.dataset_id)
        .join(Page, Page.id == GroundTruthPage.page_id)
        .join(ImageAsset, ImageAsset.id == Page.image_asset_id)
        .where(EvaluationDataset.project_id == project_id, ImageAsset.sha256 == sha,
               GroundTruthPage.id.not_in(select(GroundTruthWithdrawal.gt_page_id)))).all()
    for gt, ds, pg in rows:
        if ds.id == dataset.id:
            raise ApiError(409, "duplicate_ground_truth", "This page (or an identical image) is already in this dataset",
                           details={"gt_page_id": str(gt.id), "split": gt.split, "page_id": str(pg.id)})
        if gt.split != split:
            raise ApiError(409, "data_leakage",
                           f"This page (or an identical image) is in the '{gt.split}' split of dataset '{ds.name}'; "
                           f"adding it to '{split}' would leak data between splits",
                           details={"dataset": ds.name, "split": gt.split, "page_id": str(pg.id),
                                    "same_page": pg.id == page.id})


def _new_gt(db, dataset, page, segmentation_id, split, source, details, note, lines: list[tuple[uuid.UUID, str]], user):
    gt = GroundTruthPage(id=uuid.uuid4(), dataset_id=dataset.id, page_id=page.id, segmentation_id=segmentation_id,
                         split=split, source=source, source_details=details, note=note,
                         unicode_form=unicode_form([t for _, t in lines]), line_count=len(lines),
                         char_count=sum(len(t) for _, t in lines), created_by_id=user.id)
    db.add(gt)
    db.flush()
    for i, (line_id, text) in enumerate(lines):
        db.add(GroundTruthLine(gt_page_id=gt.id, line_id=line_id, order_index=i, text=text))
    db.flush()
    return gt


def add_from_transcription(db, dataset: EvaluationDataset, tx: Transcription, split: str, note: str | None, user):
    if split not in SPLITS:
        raise ApiError(422, "invalid_split", f"split must be one of {', '.join(SPLITS)}")
    page = db.get(Page, tx.page_id)
    check_leakage(db, dataset.project_id, page, split, dataset)
    cur = editorial.current_versions(db, tx)
    lines = list(db.scalars(select(TextLine).where(TextLine.segmentation_id == tx.segmentation_id).order_by(TextLine.order_index)))
    not_reviewed = [ln.order_index + 1 for ln in lines if ln.id not in cur or cur[ln.id].status != LineStatus.REVIEWED]
    if not_reviewed:
        # Unreviewed lines are still the model's own output: using them as reference would compare
        # the model with itself (master prompt §11).
        raise ApiError(409, "ground_truth_not_reviewed",
                       f"{len(not_reviewed)} line(s) are not marked reviewed; only fully reviewed transcriptions can become ground truth",
                       details={"lines": not_reviewed[:200], "count": len(not_reviewed)})
    base = None
    if tx.base_recognition_id:
        rec = db.get(Recognition, tx.base_recognition_id)
        mv = db.get(ModelVersion, rec.model_version_id)
        base = {"model": mv.model.slug, "version": mv.version, "model_version_id": str(mv.id)}
    details = {"transcription_id": str(tx.id), "revision": tx.head_revision, "corrected_from": base}
    auto_note = (f"Reference corrected from the output of {base['model']} {base['version']}; results of that model on "
                 f"this page may be favoured by post-correction bias.") if base else None
    return _new_gt(db, dataset, page, tx.segmentation_id, split, "transcription", details,
                   "\n".join(x for x in (note, auto_note) if x) or None,
                   [(ln.id, cur[ln.id].text) for ln in lines], user)


def import_xml(db, dataset: EvaluationDataset, page: Page, data: bytes, filename: str, split: str, note: str | None, user):
    if split not in SPLITS:
        raise ApiError(422, "invalid_split", f"split must be one of {', '.join(SPLITS)}")
    check_leakage(db, dataset.project_id, page, split, dataset)
    try:
        parsed = gt_import.parse(data)
        asset = db.get(ImageAsset, page.image_asset_id)
        fit = gt_import.fit_to_image(parsed, asset.width, asset.height)
    except gt_import.ImportError_ as e:
        raise ApiError(422, e.code, e.message, details=e.details) from e
    sha = hashlib.sha256(data).hexdigest()
    seg = Segmentation(id=uuid.uuid4(), page_id=page.id, source="import", input_asset_id=page.image_asset_id,
                       text_direction="horizontal-lr",
                       config={"format": parsed.format, "filename": filename, "sha256": sha, **fit},
                       software={"declared_by_file": parsed.software} if parsed.software else {}, created_by_id=user.id)
    db.add(seg)
    db.flush()
    region_ids = {}
    for i, r in enumerate(parsed.regions):
        tr = TextRegion(id=uuid.uuid4(), segmentation_id=seg.id, external_id=r.external_id, region_type=r.region_type,
                        order_index=i, boundary=r.boundary)
        db.add(tr)
        region_ids[r.external_id] = tr.id
    db.flush()
    lines = []
    for ln in (x for x in parsed.lines if x.baseline and len(x.baseline) >= 2):
        tl = TextLine(id=uuid.uuid4(), segmentation_id=seg.id, region_id=region_ids.get(ln.region_external_id),
                      external_id=ln.external_id, order_index=len(lines), baseline=ln.baseline, boundary=ln.boundary,
                      bbox=ln.bbox, tags={})
        db.add(tl)
        lines.append((tl.id, ln.text))
    db.flush()
    details = {"filename": filename, "sha256": sha, "format": parsed.format, "declared_software": parsed.software,
               "image_filename_in_file": parsed.image_filename, "segmentation_id": str(seg.id), **fit}
    return _new_gt(db, dataset, page, seg.id, split, parsed.format, details, note, lines, user)


# ---------------------------------------------------------------------------------------- scoring
def hypothesis_by_reference_line(db, recognition: Recognition) -> dict[str, str]:
    """Hypothesis text keyed by the *reference* line id. Recognitions on a segmentation derived by
    Kraken polygonisation map back through `tags.source_line_id`."""
    out = {}
    rows = db.execute(select(LineRecognition.text, TextLine.id, TextLine.tags).join(TextLine, TextLine.id == LineRecognition.line_id)
                      .where(LineRecognition.recognition_id == recognition.id)).all()
    for text, line_id, tags in rows:
        out[(tags or {}).get("source_line_id") or str(line_id)] = text
    return out


def _same_segmentation(db, gt: GroundTruthPage, recognition: Recognition) -> bool:
    seg = db.get(Segmentation, recognition.segmentation_id)
    return seg.id == gt.segmentation_id or seg.parent_segmentation_id == gt.segmentation_id


def score(db, gt: GroundTruthPage, recognition: Recognition, norm: metrics.Normalization) -> metrics.Score:
    if recognition.page_id != gt.page_id:
        raise ApiError(422, "page_mismatch", "The recognition and the ground truth belong to different pages")
    reference = [(str(ln.line_id), ln.text) for ln in gt.lines]
    markup_ref = gt.source == "transcription"
    if _same_segmentation(db, gt, recognition):
        return metrics.score_lines(reference, hypothesis_by_reference_line(db, recognition), norm, markup_ref)
    hyp = list(db.scalars(select(LineRecognition.text).where(LineRecognition.recognition_id == recognition.id)
                          .order_by(LineRecognition.order_index)))
    return metrics.score_page([t for _, t in reference], hyp, norm, markup_ref)


def store_result(db, gt, recognition, sc: metrics.Score, norm: metrics.Normalization, run_id=None, user_id=None):
    res = EvaluationResult(id=uuid.uuid4(), run_id=run_id, gt_page_id=gt.id, recognition_id=recognition.id,
                           model_version_id=recognition.model_version_id, methodology=norm.describe(),
                           alignment=sc.alignment, ref_chars=sc.ref_chars, char_errors=sc.char_errors,
                           ref_words=sc.ref_words, word_errors=sc.word_errors, cer=sc.cer, wer=sc.wer,
                           lines_matched=sc.lines_matched, lines_missing=sc.lines_missing, details=sc.details(),
                           created_by_id=user_id)
    db.add(res)
    return res


def score_run_page(db, run_id: uuid.UUID, gt_page_id: uuid.UUID, recognition_id: uuid.UUID) -> EvaluationResult:
    run = db.get(EvaluationRun, run_id)
    gt = db.get(GroundTruthPage, gt_page_id)
    rec = db.get(Recognition, recognition_id)
    norm = metrics.Normalization.model_validate({k: v for k, v in run.methodology.items() if k != "version"})
    existing = db.scalar(select(EvaluationResult).where(EvaluationResult.run_id == run.id, EvaluationResult.gt_page_id == gt.id))
    if existing:
        return existing
    res = store_result(db, gt, rec, score(db, gt, rec, norm), norm, run.id, run.created_by_id)
    db.commit()
    return res


# ------------------------------------------------------------------------------------ aggregation
DIMENSIONS = ("model", "document", "page", "collection", "language", "period", "script_type", "document_type", "split",
              "dataset")


def century_label(year: int | None) -> str | None:
    if year is None:
        return None
    c = (year - 1) // 100 + 1
    suffix = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
    return f"{c}{suffix} century"


def period_label(start: int | None, end: int | None) -> str | None:
    """Century bucket from the document's recorded date range (never guessed)."""
    a, b = century_label(start), century_label(end)
    if a and b and a != b:
        return f"{a} – {b}"
    return a or b


def results_table(db, project_id: uuid.UUID, dataset_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None):
    """Latest result per (run or ad-hoc, gt page) with all grouping attributes."""
    q = (select(EvaluationResult, GroundTruthPage, EvaluationDataset, Page, Document, Collection, ModelVersion)
         .join(GroundTruthPage, GroundTruthPage.id == EvaluationResult.gt_page_id)
         .join(EvaluationDataset, EvaluationDataset.id == GroundTruthPage.dataset_id)
         .join(Page, Page.id == GroundTruthPage.page_id)
         .join(Document, Document.id == Page.document_id)
         .join(Collection, Collection.id == Document.collection_id)
         .join(ModelVersion, ModelVersion.id == EvaluationResult.model_version_id)
         .where(EvaluationDataset.project_id == project_id))
    if dataset_id:
        q = q.where(EvaluationDataset.id == dataset_id)
    if run_id:
        q = q.where(EvaluationResult.run_id == run_id)
    rows = []
    for res, gt, ds, page, doc, col, mv in db.execute(q.order_by(EvaluationResult.created_at)).all():
        rows.append({
            "result_id": str(res.id), "run_id": str(res.run_id) if res.run_id else None,
            "recognition_id": str(res.recognition_id), "gt_page_id": str(gt.id), "split": gt.split,
            "dataset": ds.name, "dataset_id": str(ds.id),
            "model": f"{mv.model.name} · {mv.version}", "model_version_id": str(mv.id),
            "document": doc.title, "document_id": str(doc.id), "page": f"{doc.title} · p. {page.sequence}",
            "page_id": str(page.id), "collection": col.title, "collection_id": str(col.id),
            "language": ", ".join(doc.languages) if doc.languages else None,
            "languages": doc.languages or [],
            "period": period_label(doc.date_start, doc.date_end),
            "script_type": doc.script_type, "document_type": doc.document_type,
            "alignment": res.alignment, "methodology": res.methodology,
            "ref_chars": res.ref_chars, "char_errors": res.char_errors, "ref_words": res.ref_words,
            "word_errors": res.word_errors, "cer": res.cer, "wer": res.wer,
            "lines_matched": res.lines_matched, "lines_missing": res.lines_missing,
            "created_at": res.created_at.isoformat(), "note": gt.note,
        })
    return rows


def group(rows: list[dict], by: str) -> list[dict]:
    if by not in DIMENSIONS:
        raise ApiError(422, "invalid_dimension", f"group_by must be one of {', '.join(DIMENSIONS)}")
    buckets: dict[str, list[dict]] = defaultdict(list)
    methods: dict[str, set] = defaultdict(set)
    for r in rows:
        keys = r["languages"] if by == "language" and r["languages"] else [r.get(by) or "(not recorded)"]
        for k in keys:
            buckets[k].append(r)
            methods[k].add(str(sorted(r["methodology"].items())))
    out = []
    for k, rs in sorted(buckets.items()):
        agg = metrics.aggregate(rs)
        # Results computed with different normalisations are not comparable: flag it.
        out.append({"key": k, **agg, "models": sorted({r["model"] for r in rs}),
                    "mixed_methodology": len(methods[k]) > 1,
                    "alignments": sorted({r["alignment"] for r in rs})})
    return out
