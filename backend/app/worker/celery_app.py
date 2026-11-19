"""Celery application. Queues:
* ``default``      – light housekeeping
* ``recognition``  – Kraken segmentation/recognition (CPU/GPU heavy)
* ``models``       – model download/installation/validation (network, disk)
Training (Phase 6) runs on its own queue `training`, served by a dedicated worker.
"""
from __future__ import annotations

import platform

from celery import Celery
from kombu import Queue

from app.config import get_settings

s = get_settings()
celery_app = Celery("scriptorium", broker=s.redis_url, backend=s.redis_url, include=["app.worker.tasks"])
celery_app.conf.update(
    task_queues=[Queue("default"), Queue("recognition"), Queue("models"), Queue("training")],
    task_default_queue="default",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=7 * 24 * 3600,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_always_eager=s.celery_eager,
    # Unacknowledged messages return to the queue only after this delay; must exceed the job time limit.
    broker_transport_options={"visibility_timeout": s.job_soft_time_limit_seconds + 600},
    beat_schedule={
        "recover-stale-jobs": {"task": "jobs.recover", "schedule": 60.0},
        "check-model-updates": {"task": "models.check_updates", "schedule": 24 * 3600.0},
        "data-retention": {"task": "maintenance.retention", "schedule": float(get_settings().retention_interval_seconds)},
    },
    task_eager_propagates=True,
)


@celery_app.task(name="system.ping")
def ping() -> dict:
    """Round-trip probe used by health checks and tests."""
    from app import __version__

    return {"pong": True, "version": __version__, "host": platform.node(), "python": platform.python_version()}


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _recover_on_start(**_):
    """A restarting worker immediately re-queues jobs orphaned by a previous crash."""
    try:
        from app.worker.pipeline import recover_stale_jobs

        recover_stale_jobs()
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("stale job recovery failed at startup")
