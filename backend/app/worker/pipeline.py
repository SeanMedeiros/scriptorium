"""Job execution (runs inside Celery workers).

All outputs of a job (derived image, segmentation, regions, lines, recognition,
line texts) are written in ONE transaction at the end: a failed or cancelled job
leaves no partial results behind. Progress and log entries are committed
separately in short transactions so that users can follow the job.
"""
from __future__ import annotations

import io
import json
import logging
import platform
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db import session_factory
from app.models import (
    AssetKind,
    Document,
    ImageAsset,
    JobStatus,
    JobType,
    LineRecognition,
    ModelStatus,
    ModelVersion,
    Page,
    Recognition,
    RecognitionJob,
    Segmentation,
    TextLine,
    TextRegion,
)
from app.services import audit
from app.services.ingest import IngestError, extract_pages, load_image
from app.storage import get_storage

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def job_log(job_id: uuid.UUID, level: str, message: str, **extra) -> None:
    entry = {"ts": _now().isoformat(), "level": level, "message": message, **extra}
    with session_factory()() as s:
        s.execute(text("UPDATE recognition_jobs SET log = log || CAST(:e AS jsonb) WHERE id = :id"),
                  {"e": json.dumps([entry], default=str), "id": job_id})
        s.commit()
    log.info(message, extra={"job_id": str(job_id), **extra})


def _set(job_id: uuid.UUID, **values) -> None:
    with session_factory()() as s:
        s.execute(update(RecognitionJob).where(RecognitionJob.id == job_id).values(**values))
        s.commit()


def _status(job_id: uuid.UUID) -> JobStatus | None:
    with session_factory()() as s:
        return s.scalar(select(RecognitionJob.status).where(RecognitionJob.id == job_id))


# ---------------------------------------------------------------- ingestion
def ingest_document(document_id: uuid.UUID) -> dict:
    db = session_factory()()
    try:
        doc = db.get(Document, document_id)
        if doc is None or doc.ingest_status == "ready":
            return {"skipped": True}
        original = db.get(ImageAsset, doc.source_asset_id)
        storage = get_storage()
        try:
            pages = extract_pages(storage, original, doc.created_by_id)
        except IngestError as e:
            doc.ingest_status, doc.ingest_error = "failed", {"code": e.code, "message": e.message, "details": e.details}
            audit.record(db, "document.ingest_failed", entity_type="document", entity_id=doc.id,
                         details={"code": e.code})
            db.commit()
            return {"failed": e.code}
        start = (db.scalar(text("SELECT coalesce(max(sequence), 0) FROM pages WHERE document_id = :d"), {"d": doc.id}) or 0)
        for i, p in enumerate(pages, start=1):
            if p.asset.id != original.id:
                db.add(p.asset)
                db.flush()
            db.add(Page(document_id=doc.id, sequence=start + i, image_asset_id=p.asset.id,
                        source_page_number=p.source_page_number, label=None))
        doc.ingest_status, doc.ingest_error = "ready", None
        db.commit()
        return {"pages": len(pages)}
    finally:
        db.close()


# ---------------------------------------------------------------- recognition
def _model(db: Session, mv_id: uuid.UUID | None) -> ModelVersion | None:
    if mv_id is None:
        return None
    mv = db.get(ModelVersion, mv_id)
    if mv is None or mv.status != ModelStatus.VALIDATED or not mv.file_path:
        from app.recognition.engine import EngineError

        raise EngineError("model_unavailable", "The selected model version is not installed and validated", "load_model",
                          {"model_version_id": str(mv_id), "status": mv.status.value if mv else None})
    return mv


def _derived_asset(db: Session, storage, parent: ImageAsset, im, pipeline_cfg: dict, user_id):
    """Run preprocessing and store (or reuse) the derived asset."""
    from app.recognition.preprocessing import Pipeline, run_pipeline

    pipeline = Pipeline.model_validate(pipeline_cfg or {})
    if pipeline.is_identity:
        return parent, im, None
    out, prov = run_pipeline(im, pipeline)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    obj = storage.put_stream("derived", buf)
    existing = db.scalar(select(ImageAsset).where(ImageAsset.sha256 == obj.sha256, ImageAsset.parent_asset_id == parent.id,
                                                  ImageAsset.kind == AssetKind.DERIVED))
    if existing:
        return existing, out, prov
    asset = ImageAsset(id=uuid.uuid4(), kind=AssetKind.DERIVED, storage_key=obj.key, sha256=obj.sha256, size_bytes=obj.size,
                       mime_type="image/png", width=out.width, height=out.height, dpi=parent.dpi,
                       parent_asset_id=parent.id, derivation={"operation": "preprocessing", **prov}, created_by_id=user_id)
    return asset, out, prov


class _Heartbeat:
    """Background thread stamping `heartbeat_at` while a job runs, so that jobs of a
    lost worker can be detected and re-queued (see recover_stale_jobs)."""

    def __init__(self, job_id: uuid.UUID, every: int):
        import threading

        self.job_id, self.every = job_id, every
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            try:
                _set(self.job_id, heartbeat_at=_now())
            except Exception:  # noqa: BLE001 — a missed beat must never kill the job
                log.warning("heartbeat failed", extra={"job_id": str(self.job_id)})

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)


def run_recognition_job(job_id: uuid.UUID, celery_task_id: str | None = None) -> dict:
    from app.config import get_settings

    s_ = get_settings()
    # Claim the job atomically (only QUEUED jobs start; cancelled ones are skipped).
    with session_factory()() as s:
        claimed = s.execute(
            update(RecognitionJob).where(RecognitionJob.id == job_id, RecognitionJob.status == JobStatus.QUEUED)
            .values(status=JobStatus.PROCESSING, started_at=_now(), heartbeat_at=_now(),
                    attempts=RecognitionJob.attempts + 1, worker=platform.node(), celery_task_id=celery_task_id,
                    stage="starting", progress=0.0)
        ).rowcount
        s.commit()
    if not claimed:
        return {"skipped": True, "reason": "not queued"}
    with _Heartbeat(job_id, s_.job_heartbeat_seconds):
        return _execute(job_id)


def recover_stale_jobs() -> list[str]:
    """Re-queue PROCESSING jobs whose worker stopped sending heartbeats (crash, OOM, node loss).
    Jobs that already used all attempts are marked FAILED instead. Returns affected job ids."""
    from datetime import timedelta

    from app.config import get_settings

    s_ = get_settings()
    cutoff = _now() - timedelta(seconds=s_.job_stale_after_seconds)
    requeue: list[str] = []
    with session_factory()() as s:
        stale = s.scalars(select(RecognitionJob).where(
            RecognitionJob.status == JobStatus.PROCESSING,
            (RecognitionJob.heartbeat_at < cutoff) | RecognitionJob.heartbeat_at.is_(None),
        ).with_for_update(skip_locked=True)).all()
        for j in stale:
            entry = {"ts": _now().isoformat(), "level": "warning", "worker": j.worker,
                     "last_heartbeat": j.heartbeat_at.isoformat() if j.heartbeat_at else None}
            if j.attempts >= s_.job_max_attempts:
                j.error = {"code": "worker_lost", "message": f"Worker lost {j.attempts} times; giving up",
                           "stage": j.stage or "unknown", "details": {}}
                j.status, j.completed_at, j.stage = JobStatus.FAILED, _now(), None
                j.log = [*(j.log or []), {**entry, "level": "error", "message": "worker lost; maximum attempts reached"}]
            else:
                j.status, j.stage, j.progress = JobStatus.QUEUED, None, 0.0
                j.log = [*(j.log or []), {**entry, "message": "worker lost (no heartbeat); job re-queued"}]
                requeue.append(str(j.id))
        s.commit()
    if requeue:
        from app.worker.tasks import run_job

        for jid in requeue:
            run_job.apply_async(args=[jid], task_id=f"{jid}-r{uuid.uuid4().hex[:6]}")
    return requeue


def _execute(job_id: uuid.UUID) -> dict:
    from app.recognition import engine

    db = session_factory()()
    t_start = time.perf_counter()
    try:
        job = db.get(RecognitionJob, job_id)
        page = db.get(Page, job.page_id)
        storage = get_storage()
        cfg = job.config or {}
        software = engine.software_versions()
        job_log(job_id, "info", "job started", worker=platform.node(), software=software)

        seg_mv = _model(db, job.segmentation_model_version_id)
        rec_mv = _model(db, job.recognition_model_version_id)
        device = cfg.get("device", "cpu")

        new_rows: list = []
        if job.job_type in (JobType.SEGMENT, JobType.SEGMENT_RECOGNIZE):
            _set(job_id, stage="preprocessing", progress=0.02)
            original_asset = db.get(ImageAsset, page.image_asset_id)
            im = load_image(storage, original_asset)
            input_asset, im, prov = _derived_asset(db, storage, original_asset, im, cfg.get("preprocessing"), job.submitted_by_id)
            if prov:
                job_log(job_id, "info", "preprocessing applied", operations=prov["operations"],
                        geometry_preserving=prov["geometry_preserving"])
                if db.get(ImageAsset, input_asset.id) is None:
                    new_rows.append(input_asset)
            _set(job_id, stage="segmentation", progress=0.05)
            job_log(job_id, "info", "segmentation started", model=f"{seg_mv.model.slug} {seg_mv.version}")
            seg_out = engine.segment(im, model_path=Path(seg_mv.file_path), model_sha256=seg_mv.sha256,
                                     text_direction=cfg.get("text_direction", "horizontal-lr"), device=device)
            job_log(job_id, "info", "segmentation finished", lines=len(seg_out.lines), regions=len(seg_out.regions),
                    ms=seg_out.duration_ms)
            if not seg_out.lines:
                raise engine.EngineError("no_lines_detected", "The segmentation model detected no text lines on this page",
                                         "segmentation", {"regions": len(seg_out.regions)})
            segmentation = Segmentation(id=uuid.uuid4(), page_id=page.id, job_id=job.id, source="kraken",
                                        model_version_id=seg_mv.id, input_asset_id=input_asset.id,
                                        text_direction=seg_out.text_direction,
                                        config={"text_direction": cfg.get("text_direction", "horizontal-lr"),
                                                "preprocessing": cfg.get("preprocessing") or {"operations": []}},
                                        software=software, duration_ms=seg_out.duration_ms, created_by_id=job.submitted_by_id)
            new_rows.append(segmentation)
            region_ids: dict[str, uuid.UUID] = {}
            for i, r in enumerate(seg_out.regions):
                rid = uuid.uuid4()
                region_ids[r.external_id] = rid
                new_rows.append(TextRegion(id=rid, segmentation_id=segmentation.id, external_id=r.external_id,
                                           region_type=r.region_type, order_index=i, boundary=r.boundary, tags=r.tags))
            line_rows = []
            for i, ln in enumerate(seg_out.lines):
                lr = TextLine(id=uuid.uuid4(), segmentation_id=segmentation.id,
                              region_id=next((region_ids[x] for x in ln.region_external_ids if x in region_ids), None),
                              external_id=ln.external_id, order_index=i, baseline=ln.baseline, boundary=ln.boundary,
                              bbox=ln.bbox, tags=_jsonable(ln.tags))
                line_rows.append(lr)
            new_rows.extend(line_rows)
            seg_lines = [dict(external_id=ln.external_id, baseline=ln.baseline, boundary=ln.boundary,
                              region_external_ids=ln.region_external_ids) for ln in seg_out.lines]
            seg_regions = [dict(external_id=r.external_id, region_type=r.region_type, boundary=r.boundary) for r in seg_out.regions]
            text_direction = seg_out.text_direction
        else:
            segmentation = db.get(Segmentation, job.input_segmentation_id)
            if segmentation is None or segmentation.page_id != page.id:
                raise engine.EngineError("segmentation_missing", "The input segmentation does not exist for this page", "load")
            input_asset = db.get(ImageAsset, segmentation.input_asset_id)
            im = load_image(storage, input_asset)
            line_rows = list(segmentation.lines)
            missing = [ln for ln in line_rows if not ln.boundary]
            if missing:
                # Hand-drawn baselines: Kraken computes the polygons; the result is a NEW derived
                # segmentation (the manual one stays as the researcher saved it).
                job_log(job_id, "info", "computing line polygons with Kraken", lines=len(missing))
                polys = engine.compute_boundaries(im, [ln.baseline for ln in missing])
                computed = {ln.id: poly for ln, poly in zip(missing, polys)}
                failed = [str(ln.id) for ln, poly in zip(missing, polys) if not poly]
                if failed:
                    raise engine.EngineError("polygonization_failed", "Kraken could not compute a polygon for some lines",
                                             "segmentation", {"line_ids": failed})
                derived = Segmentation(id=uuid.uuid4(), page_id=page.id, job_id=job.id, source="kraken",
                                       parent_segmentation_id=segmentation.id, model_version_id=None,
                                       input_asset_id=input_asset.id, text_direction=segmentation.text_direction,
                                       config={"operation": "calculate_polygonal_environment",
                                               "computed_for_lines": len(missing)},
                                       software=engine.software_versions(), created_by_id=job.submitted_by_id)
                new_rows.append(derived)
                new_lines = []
                for ln in line_rows:
                    b = ln.boundary or computed[ln.id]
                    xs, ys = [p[0] for p in b], [p[1] for p in b]
                    new_lines.append(TextLine(id=uuid.uuid4(), segmentation_id=derived.id, region_id=None,
                                              external_id=ln.external_id, order_index=ln.order_index, baseline=ln.baseline,
                                              boundary=b, bbox=[min(xs), min(ys), max(xs), max(ys)],
                                              tags={**(ln.tags or {}), "source_line_id": str(ln.id)}))
                new_rows.extend(new_lines)
                segmentation, line_rows = derived, new_lines
            seg_lines = [dict(external_id=ln.external_id or str(ln.id), baseline=ln.baseline, boundary=ln.boundary,
                              region_external_ids=[]) for ln in line_rows]
            seg_regions = []
            text_direction = segmentation.text_direction

        recognition = None
        if job.job_type in (JobType.RECOGNIZE, JobType.SEGMENT_RECOGNIZE):
            _set(job_id, stage="recognition", progress=0.3)
            job_log(job_id, "info", "recognition started", model=f"{rec_mv.model.slug} {rec_mv.version}", lines=len(line_rows))
            kseg = engine.build_kraken_segmentation(seg_lines, seg_regions, text_direction)
            t0 = time.perf_counter()
            last = [0.0]

            def should_cancel() -> bool:
                return _status(job_id) == JobStatus.CANCELLED

            def on_progress(done: int, total: int) -> None:
                if time.perf_counter() - last[0] > 2 or done == total:
                    last[0] = time.perf_counter()
                    _set(job_id, progress=round(0.3 + 0.65 * done / max(total, 1), 3))

            results = list(engine.recognize(im, kseg, model_path=Path(rec_mv.file_path), model_sha256=rec_mv.sha256,
                                            device=device, should_cancel=should_cancel, on_progress=on_progress))
            rec_ms = int((time.perf_counter() - t0) * 1000)
            recognition = Recognition(id=uuid.uuid4(), segmentation_id=segmentation.id, page_id=page.id, job_id=job.id,
                                      model_version_id=rec_mv.id, input_asset_id=input_asset.id,
                                      config={"device": device, "decoder": "greedy", "padding": 16},
                                      software=software, duration_ms=rec_ms, line_count=len(results),
                                      char_count=sum(len(r.text) for r in results), created_by_id=job.submitted_by_id)
            new_rows.append(recognition)
            for r in results:
                new_rows.append(LineRecognition(recognition_id=recognition.id, line_id=line_rows[r.order_index].id,
                                                order_index=r.order_index, text=r.text, confidences=r.confidences,
                                                mean_confidence=r.mean_confidence, cuts=r.cuts))
            job_log(job_id, "info", "recognition finished", lines=len(results), chars=recognition.char_count, ms=rec_ms)

        if _status(job_id) == JobStatus.CANCELLED:
            raise engine.Cancelled()
        # Single atomic write of every output.
        _set(job_id, stage="saving", progress=0.97)
        for row in new_rows:
            db.add(row)
            db.flush()
        job = db.get(RecognitionJob, job_id)
        db.refresh(job)
        job.status, job.completed_at, job.progress, job.stage = JobStatus.COMPLETED, _now(), 1.0, None
        job.output_segmentation_id = segmentation.id
        job.output_recognition_id = recognition.id if recognition else None
        audit.record(db, "recognition.completed", actor_id=job.submitted_by_id, entity_type="recognition_job",
                     entity_id=job.id, project_id=job.project_id,
                     details={"page_id": str(page.id), "lines": len(line_rows),
                              "model": f"{rec_mv.model.slug} {rec_mv.version}" if rec_mv else None})
        db.commit()
        job_log(job_id, "info", "job completed", total_ms=int((time.perf_counter() - t_start) * 1000))
        ev = (job.config or {}).get("evaluation")
        if ev and recognition is not None:
            # Evaluation runs: score against the ground truth in its own transaction; a scoring
            # problem is reported on the job log but never undoes the recognition output.
            try:
                from app.evaluation.service import score_run_page

                res = score_run_page(db, uuid.UUID(ev["run_id"]), uuid.UUID(ev["gt_page_id"]), recognition.id)
                job_log(job_id, "info", "evaluated against ground truth", cer=res.cer, wer=res.wer, alignment=res.alignment)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                job_log(job_id, "error", "evaluation failed", error=f"{type(e).__name__}: {e}")
        return {"status": "completed", "segmentation_id": str(segmentation.id),
                "recognition_id": str(recognition.id) if recognition else None}
    except engine.Cancelled:
        db.rollback()
        _set(job_id, completed_at=_now(), stage=None)
        job_log(job_id, "warning", "job cancelled while processing; no partial output was saved")
        return {"status": "cancelled"}
    except engine.EngineError as e:
        db.rollback()
        _fail(job_id, e.as_dict())
        return {"status": "failed", "error": e.code}
    except Exception as e:  # noqa: BLE001 — every failure must be reported on the job
        db.rollback()
        _fail(job_id, {"code": "internal_error", "message": f"{type(e).__name__}: {e}", "stage": "unknown", "details": {}},
              tb=traceback.format_exc())
        return {"status": "failed", "error": "internal_error"}
    finally:
        db.close()


def _fail(job_id: uuid.UUID, error: dict, tb: str | None = None) -> None:
    with session_factory()() as s:
        s.execute(update(RecognitionJob).where(RecognitionJob.id == job_id, RecognitionJob.status != JobStatus.CANCELLED)
                  .values(status=JobStatus.FAILED, error=error, completed_at=_now(), stage=None))
        s.commit()
    job_log(job_id, "error", error["message"], code=error["code"], stage=error.get("stage"), traceback=tb)


def mark_timeout(job_id: uuid.UUID, seconds: int) -> None:
    _fail(job_id, {"code": "timeout", "message": f"Job exceeded the time limit of {seconds} s", "stage": "unknown", "details": {}})


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return json.loads(json.dumps(v, default=str))
