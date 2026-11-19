"""Recognition models (read), jobs, segmentations and recognition outputs."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, load_project
from app.api.documents import _document, asset_out
from app.config import get_settings
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.models import (
    Collection,
    Document,
    ImageAsset,
    JobStatus,
    JobType,
    ModelStatus,
    ModelTask,
    ModelVersion,
    Page,
    Project,
    Recognition,
    RecognitionJob,
    RecognitionModel,
    Segmentation,
)
from app.schemas_recognition import (
    BatchOut,
    JobCreateIn,
    JobDetailOut,
    JobOut,
    LineRecognitionOut,
    ModelVersionDetailOut,
    ModelVersionOut,
    RecognitionOut,
    SegmentationOut,
)
from app.services import audit

router = APIRouter(tags=["recognition"])
ACTIVE = (JobStatus.QUEUED, JobStatus.PROCESSING)


def mv_out(mv: ModelVersion, detail: bool = False):
    cls = ModelVersionDetailOut if detail else ModelVersionOut
    data = {c: getattr(mv, c) for c in cls.model_fields if hasattr(mv, c)}
    data.update(model_slug=mv.model.slug, model_name=mv.model.name, task=mv.model.task, source=mv.model.source.value)
    return cls(**data)


def model_scope_filter(db, user):
    """Platform-wide models, plus institutional models of projects/institutions the user belongs to."""
    from sqlalchemy import or_

    from app.core.permissions import visible_projects_query
    from app.models import InstitutionMembership

    if user.is_platform_admin:
        return None
    projects = visible_projects_query(user).with_only_columns(Project.id)
    insts = select(InstitutionMembership.institution_id).where(InstitutionMembership.user_id == user.id)
    return or_(RecognitionModel.owner_project_id.in_(projects),
               (RecognitionModel.owner_project_id.is_(None)) & RecognitionModel.owner_institution_id.in_(insts),
               (RecognitionModel.owner_project_id.is_(None)) & RecognitionModel.owner_institution_id.is_(None))


@router.get("/models", response_model=list[ModelVersionOut], tags=["models"])
def list_models(user: CurrentUser, db: DB, task: ModelTask | None = None, usable: bool = False,
                project_id: uuid.UUID | None = None):
    stmt = select(ModelVersion).join(RecognitionModel, RecognitionModel.id == ModelVersion.model_id).order_by(RecognitionModel.name, ModelVersion.created_at)
    if task:
        stmt = stmt.where(RecognitionModel.task == task)
    if usable:
        stmt = stmt.where(ModelVersion.status == ModelStatus.VALIDATED)
    f = model_scope_filter(db, user)
    if f is not None:
        stmt = stmt.where(f)
    out = list(db.scalars(stmt))
    if project_id:
        from app.training.service import usable_in_project

        project, _ = load_project(db, user, project_id, P.VIEW)
        out = [mv for mv in out if usable_in_project(mv.model, project)]
    return [mv_out(mv) for mv in out]


@router.get("/models/{model_version_id}", response_model=ModelVersionDetailOut, tags=["models"])
def get_model(model_version_id: uuid.UUID, user: CurrentUser, db: DB):
    mv = db.get(ModelVersion, model_version_id)
    f = model_scope_filter(db, user)
    if mv is None or (f is not None and not db.scalar(select(RecognitionModel.id).where(RecognitionModel.id == mv.model_id, f))):
        raise not_found("Model version")
    return mv_out(mv, detail=True)


def _usable(db, mv_id: uuid.UUID | None, task: ModelTask, field: str, project: Project | None = None) -> ModelVersion:
    from app.training.service import usable_in_project

    mv = db.get(ModelVersion, mv_id) if mv_id else None
    if mv is None or mv.model.task != task or (project is not None and not usable_in_project(mv.model, project)):
        raise ApiError(422, "invalid_model", f"{field} must reference a {task.value} model", details={"field": field})
    if mv.status != ModelStatus.VALIDATED:
        raise ApiError(409, "model_not_validated", f"Model {mv.model.slug} {mv.version} is '{mv.status.value}', not validated")
    return mv


def _default_segmenter(db) -> ModelVersion:
    mv = db.scalar(select(ModelVersion).join(RecognitionModel, RecognitionModel.id == ModelVersion.model_id).where(
        RecognitionModel.slug == "kraken-blla", ModelVersion.status == ModelStatus.VALIDATED)
        .order_by(ModelVersion.created_at.desc()).limit(1))
    if mv is None:
        raise ApiError(409, "no_segmentation_model", "No validated segmentation model is installed "
                       "(run `python -m app.cli register-bundled`)")
    return mv


def _job_out(db, j: RecognitionJob, detail: bool = False):
    cls = JobDetailOut if detail else JobOut
    o = cls.model_validate(j)
    page = db.get(Page, j.page_id)
    doc = db.get(Document, j.document_id)
    o.page_sequence = page.sequence if page else None
    o.document_title = doc.title if doc else None
    if j.recognition_model_version_id:
        mv = db.get(ModelVersion, j.recognition_model_version_id)
        o.recognition_model = f"{mv.model.name} · {mv.version}"
    return o


def _enqueue(job: RecognitionJob) -> None:
    from app.worker.tasks import run_job

    run_job.apply_async(args=[str(job.id)], task_id=str(job.id))


@router.post("/projects/{project_id}/recognition-jobs", response_model=BatchOut, status_code=201)
def create_jobs(project_id: uuid.UUID, body: JobCreateIn, request: Request, user: CurrentUser, db: DB):
    s = get_settings()
    project, _ = load_project(db, user, project_id, P.RUN_RECOGNITION)
    # Resolve pages and verify they all belong to this project.
    page_ids = set(body.page_ids)
    if body.document_ids:
        page_ids |= set(db.scalars(select(Page.id).join(Document).where(Document.id.in_(body.document_ids))))
    if not page_ids:
        raise ApiError(422, "no_pages", "Select at least one page or document")
    if len(page_ids) > s.max_pages_per_request:
        raise ApiError(413, "too_many_pages", f"At most {s.max_pages_per_request} pages per request")
    rows = db.execute(select(Page.id, Page.document_id, Collection.project_id).join(Document, Document.id == Page.document_id)
                      .join(Collection, Collection.id == Document.collection_id).where(Page.id.in_(page_ids))).all()
    if len(rows) != len(page_ids) or any(r[2] != project.id for r in rows):
        raise ApiError(404, "not_found", "One or more pages do not exist in this project")

    seg_mv = rec_mv = None
    if body.job_type in (JobType.SEGMENT, JobType.SEGMENT_RECOGNIZE):
        seg_mv = (_usable(db, body.segmentation_model_version_id, ModelTask.SEGMENTATION, "segmentation_model_version_id", project)
                  if body.segmentation_model_version_id else _default_segmenter(db))
    if body.job_type in (JobType.RECOGNIZE, JobType.SEGMENT_RECOGNIZE):
        rec_mv = _usable(db, body.recognition_model_version_id, ModelTask.RECOGNITION, "recognition_model_version_id", project)
    if body.job_type == JobType.RECOGNIZE:
        if len(rows) != 1 or body.input_segmentation_id is None:
            raise ApiError(422, "segmentation_required", "job_type 'recognize' needs exactly one page and input_segmentation_id")
        seg = db.get(Segmentation, body.input_segmentation_id)
        if seg is None or seg.page_id != rows[0][0]:
            raise ApiError(422, "invalid_segmentation", "input_segmentation_id does not belong to the selected page")

    active = db.scalar(select(func.count(RecognitionJob.id)).where(RecognitionJob.submitted_by_id == user.id,
                                                                   RecognitionJob.status.in_(ACTIVE))) or 0
    if active + len(rows) > s.max_active_jobs_per_user:
        raise ApiError(429, "too_many_active_jobs", f"You already have {active} active jobs (limit {s.max_active_jobs_per_user})")

    batch_id = uuid.uuid4()
    config = {"preprocessing": body.preprocessing.model_dump(), "text_direction": body.text_direction,
              "device": s.recognition_device}
    order = {pid: i for i, pid in enumerate(db.scalars(select(Page.id).where(Page.id.in_(page_ids))
                                                       .order_by(Page.document_id, Page.sequence)))}
    jobs = []
    for page_id, document_id, _ in sorted(rows, key=lambda r: order[r[0]]):
        j = RecognitionJob(id=uuid.uuid4(), batch_id=batch_id, project_id=project.id, document_id=document_id,
                           page_id=page_id, job_type=body.job_type, status=JobStatus.QUEUED, submitted_by_id=user.id,
                           segmentation_model_version_id=seg_mv.id if seg_mv else None,
                           recognition_model_version_id=rec_mv.id if rec_mv else None,
                           input_segmentation_id=body.input_segmentation_id, config=config, log=[])
        db.add(j)
        jobs.append(j)
    audit.record(db, "recognition.submitted", actor_id=user.id, request=request, entity_type="batch", entity_id=batch_id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"jobs": len(jobs), "job_type": body.job_type.value,
                          "model": f"{rec_mv.model.slug} {rec_mv.version}" if rec_mv else None})
    db.commit()
    for j in jobs:
        _enqueue(j)
    return BatchOut(batch_id=batch_id, jobs=[_job_out(db, j) for j in jobs])


@router.get("/projects/{project_id}/recognition-jobs", response_model=dict)
def list_jobs(project_id: uuid.UUID, user: CurrentUser, db: DB, status: JobStatus | None = None,
              document_id: uuid.UUID | None = None, batch_id: uuid.UUID | None = None,
              limit: int = Query(100, le=500), offset: int = 0):
    project, _ = load_project(db, user, project_id, P.VIEW)
    stmt = select(RecognitionJob).where(RecognitionJob.project_id == project.id)
    if status:
        stmt = stmt.where(RecognitionJob.status == status)
    if document_id:
        stmt = stmt.where(RecognitionJob.document_id == document_id)
    if batch_id:
        stmt = stmt.where(RecognitionJob.batch_id == batch_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    counts = dict(db.execute(select(RecognitionJob.status, func.count()).where(RecognitionJob.project_id == project.id)
                             .group_by(RecognitionJob.status)).all())
    rows = db.scalars(stmt.order_by(RecognitionJob.submitted_at.desc(), RecognitionJob.id).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "counts": {s.value: counts.get(s, 0) for s in JobStatus},
            "items": [_job_out(db, j).model_dump(mode="json") for j in rows]}


def _job(db, user, job_id: uuid.UUID, perm: P) -> RecognitionJob:
    j = db.get(RecognitionJob, job_id)
    if j is None:
        raise not_found("Job")
    load_project(db, user, j.project_id, perm)
    return j


@router.get("/recognition-jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: uuid.UUID, user: CurrentUser, db: DB):
    return _job_out(db, _job(db, user, job_id, P.VIEW), detail=True)


@router.post("/recognition-jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    j = _job(db, user, job_id, P.RUN_RECOGNITION)
    if j.status not in ACTIVE:
        raise ApiError(409, "not_cancellable", f"A {j.status.value} job cannot be cancelled")
    from sqlalchemy import update

    was = j.status
    db.execute(update(RecognitionJob).where(RecognitionJob.id == j.id, RecognitionJob.status.in_(ACTIVE))
               .values(status=JobStatus.CANCELLED, completed_at=func.now() if was == JobStatus.QUEUED else None))
    audit.record(db, "recognition.cancelled", actor_id=user.id, request=request, entity_type="recognition_job",
                 entity_id=j.id, project_id=j.project_id, details={"previous_status": was.value})
    db.commit()
    if was == JobStatus.QUEUED:
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(str(j.id))
    db.refresh(j)
    return _job_out(db, j)


@router.post("/recognition-jobs/{job_id}/retry", response_model=JobOut, status_code=201)
def retry_job(job_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    j = _job(db, user, job_id, P.RUN_RECOGNITION)
    if j.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise ApiError(409, "not_retryable", "Only failed or cancelled jobs can be retried")
    nj = RecognitionJob(id=uuid.uuid4(), batch_id=j.batch_id, project_id=j.project_id, document_id=j.document_id,
                        page_id=j.page_id, job_type=j.job_type, status=JobStatus.QUEUED, submitted_by_id=user.id,
                        segmentation_model_version_id=j.segmentation_model_version_id,
                        recognition_model_version_id=j.recognition_model_version_id,
                        input_segmentation_id=j.input_segmentation_id, config=j.config, retry_of_id=j.id, log=[])
    db.add(nj)
    audit.record(db, "recognition.retried", actor_id=user.id, request=request, entity_type="recognition_job",
                 entity_id=nj.id, project_id=j.project_id, details={"retry_of": str(j.id)})
    db.commit()
    _enqueue(nj)
    return _job_out(db, nj)


# ---------------------------------------------------------------- outputs
def _page_access(db, user, page_id: uuid.UUID) -> Page:
    p = db.get(Page, page_id)
    if p is None:
        raise not_found("Page")
    _document(db, user, p.document_id, P.VIEW)
    return p


@router.get("/pages/{page_id}/segmentations", response_model=list[SegmentationOut])
def page_segmentations(page_id: uuid.UUID, user: CurrentUser, db: DB):
    p = _page_access(db, user, page_id)
    out = []
    for s in db.scalars(select(Segmentation).where(Segmentation.page_id == p.id).order_by(Segmentation.created_at.desc())):
        o = SegmentationOut.model_validate(s)
        o.regions, o.lines = [], []
        out.append(o)
    return out


@router.get("/segmentations/{segmentation_id}", response_model=SegmentationOut)
def get_segmentation(segmentation_id: uuid.UUID, user: CurrentUser, db: DB):
    s = db.get(Segmentation, segmentation_id)
    if s is None:
        raise not_found("Segmentation")
    _page_access(db, user, s.page_id)
    o = SegmentationOut.model_validate(s)
    o.input_asset = asset_out(db.get(ImageAsset, s.input_asset_id))
    return o


def _rec_out(db, r: Recognition, with_lines: bool) -> RecognitionOut:
    o = RecognitionOut.model_validate(r)
    mv = db.get(ModelVersion, r.model_version_id)
    o.model = f"{mv.model.name} · {mv.version}"
    o.unicode_normalization = mv.unicode_normalization
    o.lines = [LineRecognitionOut.model_validate(x) for x in r.lines] if with_lines else []
    return o


@router.get("/pages/{page_id}/recognitions", response_model=list[RecognitionOut])
def page_recognitions(page_id: uuid.UUID, user: CurrentUser, db: DB):
    p = _page_access(db, user, page_id)
    rs = db.scalars(select(Recognition).where(Recognition.page_id == p.id).order_by(Recognition.created_at.desc()))
    return [_rec_out(db, r, False) for r in rs]


@router.get("/recognitions/{recognition_id}", response_model=RecognitionOut)
def get_recognition(recognition_id: uuid.UUID, user: CurrentUser, db: DB):
    r = db.get(Recognition, recognition_id)
    if r is None:
        raise not_found("Recognition")
    _page_access(db, user, r.page_id)
    return _rec_out(db, r, True)
