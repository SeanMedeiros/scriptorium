"""Ground-truth datasets, evaluation runs and CER/WER results (master prompt §11)."""
from __future__ import annotations

import uuid
from collections import Counter

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.evaluation import metrics
from app.evaluation import service as ev
from app.models import (
    Collection,
    Document,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    GroundTruthPage,
    GroundTruthWithdrawal,
    JobStatus,
    ModelVersion,
    Page,
    Recognition,
    RecognitionJob,
    Transcription,
)
from app.models.evaluation import SPLITS
from app.services import audit
from app.services import jobs as jobsvc

router = APIRouter(tags=["evaluation"])
MAX_GT_BYTES = 25 * 1024 * 1024


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class FromTranscriptionIn(BaseModel):
    transcription_id: uuid.UUID
    split: str = "test"
    note: str | None = Field(default=None, max_length=2000)


class WithdrawIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RunIn(BaseModel):
    model_version_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    split: str = "test"
    normalization: metrics.Normalization = Field(default_factory=metrics.Normalization)


class ScoreIn(BaseModel):
    gt_page_id: uuid.UUID
    recognition_id: uuid.UUID
    normalization: metrics.Normalization = Field(default_factory=metrics.Normalization)
    save: bool = False


def _dataset(db, user, dataset_id: uuid.UUID, perm: P) -> tuple[EvaluationDataset, object]:
    ds = db.get(EvaluationDataset, dataset_id)
    if ds is None:
        raise not_found("evaluation dataset")
    project, _ = load_project(db, user, ds.project_id, perm)
    return ds, project


def _gt(db, user, gt_id: uuid.UUID, perm: P):
    gt = db.get(GroundTruthPage, gt_id)
    if gt is None:
        raise not_found("ground-truth page")
    ds, project = _dataset(db, user, gt.dataset_id, perm)
    return gt, ds, project


def _withdrawn(db, ids) -> set:
    return set(db.scalars(select(GroundTruthWithdrawal.gt_page_id).where(GroundTruthWithdrawal.gt_page_id.in_(ids))))


def _gt_out(db, gt: GroundTruthPage, withdrawn: bool = False) -> dict:
    page = db.get(Page, gt.page_id)
    doc = db.get(Document, page.document_id)
    return {"id": str(gt.id), "page_id": str(page.id), "page_sequence": page.sequence, "document_id": str(doc.id),
            "document_title": doc.title, "segmentation_id": str(gt.segmentation_id), "split": gt.split,
            "source": gt.source, "source_details": gt.source_details, "unicode_form": gt.unicode_form, "note": gt.note,
            "line_count": gt.line_count, "char_count": gt.char_count, "created_at": gt.created_at.isoformat(),
            "withdrawn": withdrawn}


def _ds_out(db, ds: EvaluationDataset, detail: bool = False) -> dict:
    withdrawn = _withdrawn(db, [p.id for p in ds.pages])
    active = [p for p in ds.pages if p.id not in withdrawn]
    out = {"id": str(ds.id), "project_id": str(ds.project_id), "name": ds.name, "description": ds.description,
           "created_at": ds.created_at.isoformat(), "splits": dict(Counter(p.split for p in active)),
           "pages": len(active), "lines": sum(p.line_count for p in active)}
    if detail:
        out["ground_truth"] = [_gt_out(db, p, p.id in withdrawn) for p in ds.pages]
    return out


@router.get("/evaluation/methodology")
def methodology(_: CurrentUser):
    return {"version": metrics.METHODOLOGY_VERSION, "defaults": metrics.Normalization().model_dump(),
            "description": metrics.__doc__, "splits": list(SPLITS), "dimensions": list(ev.DIMENSIONS)}


@router.get("/projects/{project_id}/evaluation-datasets")
def list_datasets(project_id: uuid.UUID, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.VIEW)
    dss = db.scalars(select(EvaluationDataset).where(EvaluationDataset.project_id == project.id).order_by(EvaluationDataset.created_at))
    return [_ds_out(db, d) for d in dss]


@router.post("/projects/{project_id}/evaluation-datasets", status_code=201)
def create_dataset(project_id: uuid.UUID, body: DatasetIn, request: Request, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.REVIEW)
    if db.scalar(select(EvaluationDataset.id).where(EvaluationDataset.project_id == project.id, EvaluationDataset.name == body.name)):
        raise ApiError(409, "name_taken", "A dataset with this name already exists in the project")
    ds = EvaluationDataset(id=uuid.uuid4(), project_id=project.id, name=body.name, description=body.description,
                           created_by_id=user.id)
    db.add(ds)
    audit.record(db, "evaluation.dataset_created", actor_id=user.id, request=request, entity_type="evaluation_dataset",
                 entity_id=ds.id, project_id=project.id, institution_id=project.institution_id, details={"name": ds.name})
    db.commit()
    db.refresh(ds)
    return _ds_out(db, ds, True)


@router.get("/evaluation-datasets/{dataset_id}")
def get_dataset(dataset_id: uuid.UUID, user: CurrentUser, db: DB):
    ds, _ = _dataset(db, user, dataset_id, P.VIEW)
    return _ds_out(db, ds, True)


@router.post("/evaluation-datasets/{dataset_id}/ground-truth/from-transcription", status_code=201)
def gt_from_transcription(dataset_id: uuid.UUID, body: FromTranscriptionIn, request: Request, user: CurrentUser, db: DB):
    ds, project = _dataset(db, user, dataset_id, P.REVIEW)
    tx = db.get(Transcription, body.transcription_id)
    if tx is None:
        raise not_found("transcription")
    page = db.get(Page, tx.page_id)
    doc = db.get(Document, page.document_id)
    if db.get(Collection, doc.collection_id).project_id != project.id:
        raise not_found("transcription")
    gt = ev.add_from_transcription(db, ds, tx, body.split, body.note, user)
    audit.record(db, "evaluation.ground_truth_added", actor_id=user.id, request=request, entity_type="ground_truth_page",
                 entity_id=gt.id, project_id=project.id, institution_id=project.institution_id,
                 details={"source": "transcription", "transcription_id": str(tx.id), "revision": tx.head_revision,
                          "split": gt.split})
    db.commit()
    return _gt_out(db, gt)


@router.post("/evaluation-datasets/{dataset_id}/ground-truth/import", status_code=201)
def gt_import(dataset_id: uuid.UUID, request: Request, user: CurrentUser, db: DB,
                    page_id: uuid.UUID = Form(...), split: str = Form("test"), note: str | None = Form(None),
                    file: UploadFile = File(...)):
    ds, project = _dataset(db, user, dataset_id, P.REVIEW)
    page = db.get(Page, page_id)
    if page is None or db.get(Collection, db.get(Document, page.document_id).collection_id).project_id != project.id:
        raise not_found("page")
    data = file.file.read(MAX_GT_BYTES + 1)
    if len(data) > MAX_GT_BYTES:
        raise ApiError(413, "file_too_large", f"Ground-truth files are limited to {MAX_GT_BYTES // 1024 // 1024} MB")
    import io

    from app.services import malware

    malware.scan_or_raise(io.BytesIO(data), file.filename or "ground-truth.xml", kind="ground_truth_file", actor_id=user.id,
                          request=request, project_id=project.id, institution_id=project.institution_id)
    gt = ev.import_xml(db, ds, page, data, file.filename or "ground-truth.xml", split, note, user)
    audit.record(db, "evaluation.ground_truth_added", actor_id=user.id, request=request, entity_type="ground_truth_page",
                 entity_id=gt.id, project_id=project.id, institution_id=project.institution_id,
                 details={"source": gt.source, "filename": file.filename, "sha256": gt.source_details["sha256"],
                          "lines": gt.line_count, "split": gt.split})
    db.commit()
    return _gt_out(db, gt)


@router.get("/ground-truth/{gt_id}")
def get_gt(gt_id: uuid.UUID, user: CurrentUser, db: DB):
    gt, _, _ = _gt(db, user, gt_id, P.VIEW)
    out = _gt_out(db, gt, bool(_withdrawn(db, [gt.id])))
    out["lines"] = [{"line_id": str(ln.line_id), "order_index": ln.order_index, "text": ln.text} for ln in gt.lines]
    return out


@router.post("/ground-truth/{gt_id}/withdraw")
def withdraw_gt(gt_id: uuid.UUID, body: WithdrawIn, request: Request, user: CurrentUser, db: DB):
    gt, _, project = _gt(db, user, gt_id, P.REVIEW)
    if _withdrawn(db, [gt.id]):
        raise ApiError(409, "already_withdrawn", "This ground-truth page was already withdrawn")
    db.add(GroundTruthWithdrawal(gt_page_id=gt.id, reason=body.reason, withdrawn_by_id=user.id))
    audit.record(db, "evaluation.ground_truth_withdrawn", actor_id=user.id, request=request, entity_type="ground_truth_page",
                 entity_id=gt.id, project_id=project.id, institution_id=project.institution_id, details={"reason": body.reason})
    db.commit()
    return _gt_out(db, gt, True)


def _run_out(db, run: EvaluationRun) -> dict:
    mv = db.get(ModelVersion, run.model_version_id)
    statuses = Counter(s.value for s in db.scalars(select(RecognitionJob.status).where(RecognitionJob.batch_id == run.batch_id)))
    results = [{"ref_chars": r.ref_chars, "char_errors": r.char_errors, "ref_words": r.ref_words, "word_errors": r.word_errors}
               for r in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id))]
    agg = metrics.aggregate(results) if results else None
    done = statuses.get("completed", 0) + statuses.get("failed", 0) + statuses.get("cancelled", 0)
    return {"id": str(run.id), "dataset_id": str(run.dataset_id), "model_version_id": str(mv.id),
            "model": f"{mv.model.name} · {mv.version}", "model_slug": mv.model.slug, "split": run.split,
            "methodology": run.methodology, "page_count": run.page_count, "batch_id": str(run.batch_id),
            "jobs": dict(statuses), "scored_pages": len(results),
            "status": "completed" if done >= run.page_count else "running", "aggregate": agg,
            "created_at": run.created_at.isoformat()}


@router.post("/evaluation-datasets/{dataset_id}/runs", status_code=201)
def start_runs(dataset_id: uuid.UUID, body: RunIn, request: Request, user: CurrentUser, db: DB):
    ds, project = _dataset(db, user, dataset_id, P.RUN_RECOGNITION)
    if body.split not in SPLITS:
        raise ApiError(422, "invalid_split", f"split must be one of {', '.join(SPLITS)}")
    if body.split == "train":
        raise ApiError(422, "train_split_not_evaluated", "The training split is not used for evaluation (it would measure memorisation)")
    pages = ev.active_pages(db, ds, body.split)
    if not pages:
        raise ApiError(422, "empty_split", f"The '{body.split}' split of this dataset has no ground-truth pages")
    mvs = [jobsvc.usable_recognition_model(db, m, project) for m in dict.fromkeys(body.model_version_ids)]
    jobsvc.check_quota(db, user, len(pages) * len(mvs))
    runs, created = [], []
    for mv in mvs:
        run = EvaluationRun(id=uuid.uuid4(), dataset_id=ds.id, model_version_id=mv.id, split=body.split,
                            methodology=body.normalization.describe(), page_count=len(pages), batch_id=uuid.uuid4(),
                            created_by_id=user.id)
        db.add(run)
        db.flush()
        for gt in pages:
            from app.models import Segmentation

            j = jobsvc.recognize_on_segmentation(db, user, project, db.get(Page, gt.page_id),
                                                 db.get(Segmentation, gt.segmentation_id), mv, run.batch_id,
                                                 {"evaluation": {"run_id": str(run.id), "gt_page_id": str(gt.id)}})
            created.append(j)
        runs.append(run)
        audit.record(db, "evaluation.run_started", actor_id=user.id, request=request, entity_type="evaluation_run",
                     entity_id=run.id, project_id=project.id, institution_id=project.institution_id,
                     details={"model": f"{mv.model.slug} {mv.version}", "split": body.split, "pages": len(pages),
                              "methodology": run.methodology})
    db.commit()
    for j in created:
        jobsvc.enqueue(j)
    return [_run_out(db, r) for r in runs]


@router.get("/evaluation-datasets/{dataset_id}/runs")
def list_runs(dataset_id: uuid.UUID, user: CurrentUser, db: DB):
    ds, _ = _dataset(db, user, dataset_id, P.VIEW)
    runs = db.scalars(select(EvaluationRun).where(EvaluationRun.dataset_id == ds.id).order_by(EvaluationRun.created_at.desc()))
    return [_run_out(db, r) for r in runs]


@router.get("/evaluation-runs/{run_id}")
def get_run(run_id: uuid.UUID, user: CurrentUser, db: DB):
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise not_found("evaluation run")
    ds, project = _dataset(db, user, run.dataset_id, P.VIEW)
    out = _run_out(db, run)
    out["results"] = ev.results_table(db, project.id, run_id=run.id)
    failed = db.scalars(select(RecognitionJob).where(RecognitionJob.batch_id == run.batch_id,
                                                     RecognitionJob.status == JobStatus.FAILED))
    out["failed_jobs"] = [{"job_id": str(j.id), "page_id": str(j.page_id), "error": j.error} for j in failed]
    return out


@router.get("/projects/{project_id}/evaluation/results")
def results(project_id: uuid.UUID, user: CurrentUser, db: DB, dataset_id: uuid.UUID | None = None,
            run_id: uuid.UUID | None = None, group_by: str = "model"):
    project, _ = load_project(db, user, project_id, P.VIEW)
    rows = ev.results_table(db, project.id, dataset_id, run_id)
    return {"group_by": group_by, "groups": ev.group(rows, group_by), "rows": rows,
            "methodology_version": metrics.METHODOLOGY_VERSION}


@router.get("/evaluation-results/{result_id}")
def get_result(result_id: uuid.UUID, user: CurrentUser, db: DB):
    res = db.get(EvaluationResult, result_id)
    if res is None:
        raise not_found("evaluation result")
    gt, _, _ = _gt(db, user, res.gt_page_id, P.VIEW)
    return {"id": str(res.id), "gt_page_id": str(gt.id), "recognition_id": str(res.recognition_id),
            "methodology": res.methodology, "alignment": res.alignment, "cer": res.cer, "wer": res.wer,
            "char_accuracy": metrics.accuracy(res.cer), "word_accuracy": metrics.accuracy(res.wer),
            "ref_chars": res.ref_chars, "char_errors": res.char_errors, "ref_words": res.ref_words,
            "word_errors": res.word_errors, "lines_matched": res.lines_matched, "lines_missing": res.lines_missing,
            "details": res.details}


@router.post("/evaluation/score")
def score(body: ScoreIn, request: Request, user: CurrentUser, db: DB):
    """Score one recognition against one ground-truth page (line-aligned when the segmentation is the
    ground truth's, page-level otherwise). Optionally store the result."""
    gt, _, project = _gt(db, user, body.gt_page_id, P.RUN_RECOGNITION if body.save else P.VIEW)
    rec = db.get(Recognition, body.recognition_id)
    if rec is None or rec.page_id != gt.page_id:
        raise ApiError(422, "page_mismatch", "The recognition does not belong to the ground-truth page")
    sc = ev.score(db, gt, rec, body.normalization)
    out = {**sc.summary(), "methodology": body.normalization.describe(), "details": sc.details(), "stored": False}
    if body.save:
        res = ev.store_result(db, gt, rec, sc, body.normalization, None, user.id)
        audit.record(db, "evaluation.scored", actor_id=user.id, request=request, entity_type="evaluation_result",
                     entity_id=res.id, project_id=project.id, institution_id=project.institution_id,
                     details={"cer": sc.cer, "wer": sc.wer, "alignment": sc.alignment})
        db.commit()
        out["stored"], out["result_id"] = True, str(res.id)
    return out
