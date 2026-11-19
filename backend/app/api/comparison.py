"""Model benchmarking laboratory (master prompt §12).

All models of a comparison recognise the same page image on the same segmentation, so their
outputs are line-aligned. Differences between models are reported as normalised edit distances
between their outputs — never as accuracy. CER/WER appear only when ground truth exists.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.editorial import service as editorial
from app.evaluation import metrics
from app.evaluation import service as ev
from app.models import (
    Collection,
    Comparison,
    Document,
    EvaluationDataset,
    GroundTruthPage,
    GroundTruthWithdrawal,
    LineRecognition,
    ModelVersion,
    Page,
    Recognition,
    RecognitionJob,
    Segmentation,
    TextLine,
)
from app.services import audit
from app.services import jobs as jobsvc

router = APIRouter(tags=["comparison"])


class ComparisonIn(BaseModel):
    segmentation_id: uuid.UUID
    model_version_ids: list[uuid.UUID] = Field(min_length=2, max_length=6)


class CombineIn(BaseModel):
    primary_recognition_id: uuid.UUID
    # line key (reference line id as returned by GET) -> recognition id chosen for that line
    selections: dict[str, uuid.UUID] = Field(default_factory=dict)


def _page(db, user, page_id: uuid.UUID, perm: P):
    page = db.get(Page, page_id)
    if page is None:
        raise not_found("page")
    doc = db.get(Document, page.document_id)
    project, perms = load_project(db, user, db.get(Collection, doc.collection_id).project_id, perm)
    return page, doc, project


def _comparison(db, user, cid: uuid.UUID, perm: P):
    c = db.get(Comparison, cid)
    if c is None:
        raise not_found("comparison")
    page, doc, project = _page(db, user, c.page_id, perm)
    return c, page, doc, project


@router.post("/pages/{page_id}/comparisons", status_code=201)
def create(page_id: uuid.UUID, body: ComparisonIn, request: Request, user: CurrentUser, db: DB):
    page, _, project = _page(db, user, page_id, P.RUN_RECOGNITION)
    seg = db.get(Segmentation, body.segmentation_id)
    if seg is None or seg.page_id != page.id:
        raise ApiError(422, "invalid_segmentation", "segmentation_id does not belong to this page")
    if not seg.lines:
        raise ApiError(422, "empty_segmentation", "The segmentation has no lines")
    ids = list(dict.fromkeys(body.model_version_ids))
    if len(ids) < 2:
        raise ApiError(422, "need_two_models", "Select at least two different models")
    mvs = [jobsvc.usable_recognition_model(db, m, project) for m in ids]
    jobsvc.check_quota(db, user, len(mvs))
    c = Comparison(id=uuid.uuid4(), project_id=project.id, page_id=page.id, segmentation_id=seg.id, batch_id=uuid.uuid4(),
                   model_version_ids=[str(m.id) for m in mvs], created_by_id=user.id)
    db.add(c)
    db.flush()
    jobs = [jobsvc.recognize_on_segmentation(db, user, project, page, seg, mv, c.batch_id, {"comparison_id": str(c.id)})
            for mv in mvs]
    audit.record(db, "comparison.created", actor_id=user.id, request=request, entity_type="comparison", entity_id=c.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"page_id": str(page.id), "segmentation_id": str(seg.id),
                          "models": [f"{m.model.slug} {m.version}" for m in mvs]})
    db.commit()
    for j in jobs:
        jobsvc.enqueue(j)
    return get(c.id, user, db)


@router.get("/pages/{page_id}/comparisons")
def list_for_page(page_id: uuid.UUID, user: CurrentUser, db: DB):
    page, _, _ = _page(db, user, page_id, P.VIEW)
    cs = db.scalars(select(Comparison).where(Comparison.page_id == page.id).order_by(Comparison.created_at.desc()))
    out = []
    for c in cs:
        names = []
        for mid in c.model_version_ids:
            mv = db.get(ModelVersion, uuid.UUID(mid))
            names.append(f"{mv.model.name} · {mv.version}")
        out.append({"id": str(c.id), "created_at": c.created_at.isoformat(), "models": names})
    return out


def _line_key(ln: TextLine) -> str:
    return (ln.tags or {}).get("source_line_id") or str(ln.id)


@router.get("/comparisons/{comparison_id}")
def get(comparison_id: uuid.UUID, user: CurrentUser, db: DB):
    c, page, doc, project = _comparison(db, user, comparison_id, P.VIEW)
    seg = db.get(Segmentation, c.segmentation_id)
    ref_lines = sorted(seg.lines, key=lambda ln: ln.order_index)
    jobs = {j.recognition_model_version_id: j for j in db.scalars(select(RecognitionJob).where(RecognitionJob.batch_id == c.batch_id))}
    # Ground truth for this page on this segmentation (line-aligned), if any.
    gts = list(db.scalars(select(GroundTruthPage).join(EvaluationDataset, EvaluationDataset.id == GroundTruthPage.dataset_id)
                          .where(GroundTruthPage.page_id == page.id, EvaluationDataset.project_id == project.id,
                                 GroundTruthPage.id.not_in(select(GroundTruthWithdrawal.gt_page_id)))
                          .order_by(GroundTruthPage.created_at.desc())))
    gt = next((g for g in gts if g.segmentation_id == seg.id), gts[0] if gts else None)
    norm = metrics.Normalization()
    models, texts = [], {}
    for mid in c.model_version_ids:
        mv = db.get(ModelVersion, uuid.UUID(mid))
        j = jobs.get(mv.id)
        rec = db.get(Recognition, j.output_recognition_id) if j and j.output_recognition_id else None
        entry = {"model_version_id": mid, "model": mv.model.name, "slug": mv.model.slug, "version": mv.version,
                 "doi": mv.doi, "record_id": mv.record_id, "sha256": mv.sha256, "license": mv.license_id,
                 "unicode_normalization": mv.unicode_normalization,
                 "job_id": str(j.id) if j else None, "job_status": j.status.value if j else None,
                 "job_error": j.error if j else None, "progress": j.progress if j else None,
                 "recognition_id": str(rec.id) if rec else None, "duration_ms": rec.duration_ms if rec else None,
                 "segmentation_id": str(rec.segmentation_id) if rec else None,
                 "kraken": (rec.software or {}).get("kraken") if rec else None,
                 "line_count": rec.line_count if rec else None, "char_count": rec.char_count if rec else None,
                 "score": None}
        if rec:
            rows = db.execute(select(LineRecognition, TextLine).join(TextLine, TextLine.id == LineRecognition.line_id)
                              .where(LineRecognition.recognition_id == rec.id)).all()
            texts[mid] = {_line_key(ln): {"text": lr.text, "line_recognition_id": str(lr.id),
                                          "mean_confidence": lr.mean_confidence} for lr, ln in rows}
            if gt:
                sc = ev.score(db, gt, rec, norm)
                entry["score"] = {**sc.summary(), "gt_page_id": str(gt.id), "methodology": norm.describe()}
        models.append(entry)
    lines = []
    for ln in ref_lines:
        key = str(ln.id)
        lines.append({"key": key, "order_index": ln.order_index, "bbox": ln.bbox, "boundary": ln.boundary,
                      "baseline": ln.baseline,
                      "outputs": {mid: texts.get(mid, {}).get(key) for mid in c.model_version_ids}})
    # Pairwise differences between model outputs (normalised edit distance on the page text):
    # a measure of disagreement, NOT of accuracy.
    pair = []
    done = [m for m in c.model_version_ids if m in texts]
    for i, a in enumerate(done):
        for b in done[i + 1:]:
            ta = metrics.normalize(" ".join(texts[a].get(str(ln.id), {}).get("text", "") for ln in ref_lines), norm)
            tb = metrics.normalize(" ".join(texts[b].get(str(ln.id), {}).get("text", "") for ln in ref_lines), norm)
            d = metrics.char_distance(ta, tb)
            pair.append({"a": a, "b": b, "char_distance": d, "normalised": d / max(len(ta), len(tb), 1),
                         "lines_differing": sum(1 for ln in ref_lines
                                                if metrics.normalize(texts[a].get(str(ln.id), {}).get("text", ""), norm)
                                                != metrics.normalize(texts[b].get(str(ln.id), {}).get("text", ""), norm))})
    gt_lines = {str(g.line_id): g.text for g in gt.lines} if gt and gt.segmentation_id == seg.id else None
    return {"id": str(c.id), "page_id": str(page.id), "page_sequence": page.sequence, "document_id": str(doc.id),
            "document_title": doc.title, "project_id": str(project.id), "segmentation_id": str(seg.id),
            "segmentation_source": seg.source, "image_asset_id": str(page.image_asset_id),
            "created_at": c.created_at.isoformat(), "models": models, "lines": lines, "pairwise": pair,
            "ground_truth": ({"gt_page_id": str(gt.id), "aligned": gt.segmentation_id == seg.id, "note": gt.note,
                              "lines": gt_lines} if gt else None),
            "note": "Model confidences are uncalibrated scores reported by the network, not probabilities of correctness."}


@router.post("/comparisons/{comparison_id}/combine", status_code=201)
def combine(comparison_id: uuid.UUID, body: CombineIn, request: Request, user: CurrentUser, db: DB):
    c, page, doc, project = _comparison(db, user, comparison_id, P.TRANSCRIBE)
    jobs = list(db.scalars(select(RecognitionJob).where(RecognitionJob.batch_id == c.batch_id)))
    recs = {j.output_recognition_id: db.get(Recognition, j.output_recognition_id) for j in jobs if j.output_recognition_id}
    primary = recs.get(body.primary_recognition_id)
    if primary is None:
        raise ApiError(422, "invalid_recognition", "primary_recognition_id is not a completed output of this comparison")
    labels = {}
    for rid, r in recs.items():
        mv = db.get(ModelVersion, r.model_version_id)
        labels[rid] = f"{mv.model.slug} {mv.version}"
    # Lines of each recognition keyed by the comparison's reference line.
    by_rec = {}
    for rid in recs:
        rows = db.execute(select(LineRecognition, TextLine).join(TextLine, TextLine.id == LineRecognition.line_id)
                          .where(LineRecognition.recognition_id == rid)).all()
        by_rec[rid] = {_line_key(ln): lr for lr, ln in rows}
    primary_lines = db.scalars(select(TextLine).where(TextLine.segmentation_id == primary.segmentation_id)).all()
    chosen, origins = {}, {}
    for ln in primary_lines:
        key = _line_key(ln)
        rid = body.selections.get(key, primary.id)
        if rid not in recs:
            raise ApiError(422, "invalid_selection", f"Recognition {rid} is not part of this comparison", details={"line": key})
        lr = by_rec[rid].get(key)
        if lr is not None:
            chosen[ln.id] = lr
            origins[labels[rid]] = origins.get(labels[rid], 0) + 1
    unknown = set(body.selections) - {_line_key(ln) for ln in primary_lines}
    if unknown:
        raise ApiError(422, "invalid_selection", "Some selected lines are not on this page", details={"lines": sorted(unknown)[:50]})
    t = editorial.create_combined(db, primary, chosen, author_id=user.id, project_policy=project.transcription_policy,
                                  comparison_id=c.id, labels=labels)
    audit.record(db, "comparison.combined", actor_id=user.id, request=request, entity_type="transcription", entity_id=t.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"comparison_id": str(c.id), "lines_by_model": origins})
    db.commit()
    return {"transcription_id": str(t.id), "lines_by_model": origins}
