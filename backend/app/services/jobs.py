"""Submission of recognition jobs on an existing segmentation (evaluation runs, comparisons).

Shares the rules of the public job endpoint: validated models only, per-user active-job quota.
The caller commits and then calls `enqueue` for each job.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.config import get_settings
from app.core.errors import ApiError
from app.models import (JobStatus, JobType, ModelStatus, ModelTask, ModelVersion, Page, Project, RecognitionJob,
                        Segmentation, User)

ACTIVE = (JobStatus.QUEUED, JobStatus.PROCESSING)


def usable_recognition_model(db, mv_id: uuid.UUID, project: Project | None = None) -> ModelVersion:
    from app.training.service import usable_in_project

    mv = db.get(ModelVersion, mv_id)
    if mv is None or mv.model.task != ModelTask.RECOGNITION or (project is not None and not usable_in_project(mv.model, project)):
        raise ApiError(422, "invalid_model", "model_version_id must reference a recognition model")
    if mv.status != ModelStatus.VALIDATED:
        raise ApiError(409, "model_not_validated", f"Model {mv.model.slug} {mv.version} is '{mv.status.value}', not validated")
    return mv


def check_quota(db, user: User, n: int) -> None:
    s = get_settings()
    active = db.scalar(select(func.count(RecognitionJob.id)).where(RecognitionJob.submitted_by_id == user.id,
                                                                   RecognitionJob.status.in_(ACTIVE))) or 0
    if active + n > s.max_active_jobs_per_user:
        raise ApiError(429, "too_many_active_jobs", f"You already have {active} active jobs (limit {s.max_active_jobs_per_user})")


def recognize_on_segmentation(db, user: User, project: Project, page: Page, segmentation: Segmentation,
                              mv: ModelVersion, batch_id: uuid.UUID, extra: dict | None = None) -> RecognitionJob:
    s = get_settings()
    j = RecognitionJob(id=uuid.uuid4(), batch_id=batch_id, project_id=project.id, document_id=page.document_id,
                       page_id=page.id, job_type=JobType.RECOGNIZE, status=JobStatus.QUEUED, submitted_by_id=user.id,
                       recognition_model_version_id=mv.id, input_segmentation_id=segmentation.id,
                       config={"preprocessing": None, "text_direction": segmentation.text_direction,
                               "device": s.recognition_device, **(extra or {})}, log=[])
    db.add(j)
    return j


def enqueue(job: RecognitionJob) -> None:
    from app.worker.tasks import run_job

    run_job.apply_async(args=[str(job.id)], task_id=str(job.id))
