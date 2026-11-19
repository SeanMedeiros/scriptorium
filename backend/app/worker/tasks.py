"""Celery task definitions (thin wrappers around app.worker.pipeline)."""
from __future__ import annotations

import uuid

from celery.exceptions import SoftTimeLimitExceeded

from app.config import get_settings
from app.worker.celery_app import celery_app


@celery_app.task(name="ingest.document", queue="default", acks_late=True)
def ingest_document(document_id: str) -> dict:
    from app.worker.pipeline import ingest_document as run

    return run(uuid.UUID(document_id))


@celery_app.task(name="recognition.run_job", bind=True, queue="recognition", acks_late=True,
                 soft_time_limit=get_settings().job_soft_time_limit_seconds)
def run_job(self, job_id: str) -> dict:
    from app.worker.pipeline import mark_timeout, run_recognition_job

    try:
        return run_recognition_job(uuid.UUID(job_id), celery_task_id=self.request.id)
    except SoftTimeLimitExceeded:
        mark_timeout(uuid.UUID(job_id), get_settings().job_soft_time_limit_seconds)
        return {"status": "failed", "error": "timeout"}


@celery_app.task(name="models.validate", queue="models")
def validate_model(model_version_id: str) -> dict:
    from app.db import session_factory
    from app.models import ModelVersion
    from app.registry.local import validate

    with session_factory()() as db:
        mv = db.get(ModelVersion, uuid.UUID(model_version_id))
        validate(db, mv)
        db.commit()
        return {"status": mv.status.value}


@celery_app.task(name="maintenance.retention", queue="default")
def retention_run() -> dict:
    """Scheduled data-retention run (export expiry, unreferenced assets, orphan objects)."""
    from app.db import session_factory
    from app.services import retention

    with session_factory()() as db:
        r = retention.run(db, apply=True)
    return {k: r[k] for k in ("assets_removed", "objects_removed", "bytes_freed", "exports_expired_count")}


@celery_app.task(name="jobs.recover", queue="default")
def recover_jobs() -> dict:
    from app.db import session_factory
    from app.training.service import recover_stale
    from app.worker.pipeline import recover_stale_jobs

    with session_factory()() as db:
        lost_training = recover_stale(db)
    return {"requeued": recover_stale_jobs(), "training_failed": lost_training}


@celery_app.task(name="models.install", queue="models", acks_late=True)
def install_model(model_version_id: str) -> dict:
    from app.db import session_factory
    from app.registry.manager import run_install

    with session_factory()() as db:
        mv = run_install(db, uuid.UUID(model_version_id))
        return {"status": mv.status.value if mv else None}


@celery_app.task(name="models.check_updates", queue="models")
def check_model_updates() -> dict:
    """Daily: report newer Zenodo versions (never installs them)."""
    from app.db import session_factory
    from app.registry.manager import check_updates, fail_interrupted_installs
    from app.models import ModelSource, RecognitionModel
    from sqlalchemy import select

    out = {}
    with session_factory()() as db:
        fail_interrupted_installs(db)
        for m in db.scalars(select(RecognitionModel).where(RecognitionModel.source == ModelSource.ZENODO)):
            try:
                out[m.slug] = check_updates(db, m)
            except Exception as e:  # noqa: BLE001 — one unreachable record must not stop the others
                out[m.slug] = {"error": str(e)[:200]}
        db.commit()
    return out


@celery_app.task(name="exports.build", queue="default", acks_late=True)
def build_export(export_id: str) -> dict:
    from app.db import session_factory
    from app.exports.batch import run_export
    from app.storage import get_storage

    with session_factory()() as db:
        e = run_export(db, uuid.UUID(export_id), get_storage())
        return {"status": e.status}


@celery_app.task(name="training.run", queue="training", acks_late=False)
def run_training(job_id: str) -> dict:
    """Runs on the dedicated training worker only (queue `training`)."""
    import socket

    from app.db import session_factory
    from app.storage import get_storage
    from app.training.service import run_training as run

    with session_factory()() as db:
        j = run(db, uuid.UUID(job_id), get_storage(), f"{socket.gethostname()}:{__import__('os').getpid()}")
        return {"status": j.status.value if j else None}


@celery_app.task(name="training.promote", queue="training")
def promote_checkpoint(job_id: str, name: str) -> dict:
    from app.db import session_factory
    from app.models import TrainingJob
    from app.training.service import promote_checkpoint as promote

    with session_factory()() as db:
        mv = promote(db, db.get(TrainingJob, uuid.UUID(job_id)), name)
        return {"model_version_id": str(mv.id)}
