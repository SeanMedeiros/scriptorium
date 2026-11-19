"""Training jobs: validation of a request, data preparation, orchestration and model registration.

Data preparation writes one PAGE XML (2019) file per ground-truth page with the page image, line
polygons (stored, or computed by Kraken from the baselines exactly as for recognition) and the
reference text rendered *diplomatically* (the form the source shows, as for CER). Reference lines
containing an illegible gap are excluded and counted — a model must not learn to emit "[…]".
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess  # nosec B404 — used only to launch the trusted training runner with a fixed argv (no shell)
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.config import get_settings
from app.core.errors import ApiError
from app.editorial import markup
from app.evaluation.service import active_pages
from app.models import (
    EvaluationDataset,
    ImageAsset,
    ModelSource,
    ModelStatus,
    ModelTask,
    ModelVersion,
    Page,
    Project,
    RecognitionModel,
    TextLine,
    TrainingJob,
    TrainingStatus,
)
from app.services import audit

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"
ACTIVE = (TrainingStatus.QUEUED, TrainingStatus.PREPARING, TrainingStatus.TRAINING, TrainingStatus.REGISTERING)
TERMINAL = (TrainingStatus.COMPLETED, TrainingStatus.FAILED, TrainingStatus.CANCELLED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(s: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "model"


# ------------------------------------------------------------------------------- visibility
def model_visible(db, user_project_ids: set, user_institution_ids: set, m: RecognitionModel) -> bool:
    if m.owner_project_id is None and m.owner_institution_id is None:
        return True
    if m.owner_project_id is not None:
        return m.owner_project_id in user_project_ids
    return m.owner_institution_id in user_institution_ids


def usable_in_project(m: RecognitionModel, project: Project) -> bool:
    """Institutional models are usable only in their owner project (or institution)."""
    if m.owner_project_id is not None:
        return m.owner_project_id == project.id
    if m.owner_institution_id is not None:
        return m.owner_institution_id == project.institution_id
    return True


# ------------------------------------------------------------------------------- submission
def check_request(db, project: Project, dataset: EvaluationDataset, base: ModelVersion | None, cfg: dict) -> dict:
    s = get_settings()
    if dataset.project_id != project.id:
        raise ApiError(404, "not_found", "evaluation dataset not found")
    counts = {sp: len(active_pages(db, dataset, sp)) for sp in ("train", "validation", "test")}
    if counts["train"] == 0:
        raise ApiError(422, "no_training_data", "The dataset has no page in its 'train' split")
    if counts["validation"] == 0:
        raise ApiError(422, "no_validation_data", "The dataset has no page in its 'validation' split; it is needed to "
                       "select checkpoints (the test split is never used during training)")
    if base is not None:
        if base.model.task != ModelTask.RECOGNITION or base.status != ModelStatus.VALIDATED:
            raise ApiError(409, "invalid_base_model", "The base model must be a validated recognition model")
        if not usable_in_project(base.model, project):
            raise ApiError(404, "not_found", "base model not found")
    if not 1 <= cfg["epochs"] <= s.training_max_epochs:
        raise ApiError(422, "invalid_config", f"epochs must be between 1 and {s.training_max_epochs}")
    active = db.scalar(select(TrainingJob.id).where(TrainingJob.project_id == project.id, TrainingJob.status.in_(ACTIVE)).limit(1))
    if active and s.training_max_active_per_project <= 1:
        raise ApiError(429, "training_in_progress", "A training job is already active in this project")
    return counts


# ---------------------------------------------------------------------------- data preparation
def _poly_str(points) -> str:
    return " ".join(f"{int(round(x))},{int(round(y))}" for x, y in points)


def _reference(text: str, markup_ref: bool) -> str | None:
    if not markup_ref:
        return " ".join(text.split())
    try:
        if markup.stats(text).illegible:
            return None
        return " ".join(markup.render(text, "diplomatic").split())
    except markup.MarkupError:
        return " ".join(text.split())


def prepare_data(db, job: TrainingJob, storage, work: Path, log) -> dict:
    """Write PAGE XML + images for the train and validation splits; return the data manifest."""
    return write_page_dataset(db, db.get(EvaluationDataset, job.dataset_id), storage, work, log,
                              normalization=job.config.get("normalization"), seed=job.config.get("seed"),
                              creator=f"SCRIPTORIUM training job {job.id}", splits=("train", "validation"))


def write_page_dataset(db, dataset: EvaluationDataset, storage, work: Path, log, *, normalization: str | None,
                       seed: int | None, creator: str, splits=("train", "validation"), relative_images: bool = False) -> dict:
    """Kraken-ready PAGE XML (line polygons + baselines + diplomatic reference text) for the given splits."""
    from PIL import Image

    from app.recognition import engine

    images = work / "images"
    images.mkdir(parents=True, exist_ok=True)
    manifest = {"dataset_id": str(dataset.id), "dataset": dataset.name, "splits": {}, "excluded_lines": 0,
                "empty_lines": 0, "normalization": normalization, "seed": seed,
                "software": engine.software_versions()}
    for split in splits:
        files, entries = [], []
        for gt in active_pages(db, dataset, split):
            page = db.get(Page, gt.page_id)
            asset = db.get(ImageAsset, page.image_asset_id)
            seg_lines = {ln.id: ln for ln in db.scalars(select(TextLine).where(TextLine.segmentation_id == gt.segmentation_id))}
            ext = {"image/png": ".png", "image/tiff": ".tif"}.get(asset.mime_type, ".jpg")
            img_path = images / f"{asset.sha256}{ext}"
            if not img_path.exists():
                with open(img_path, "wb") as f:
                    for chunk in storage.iter_bytes(asset.storage_key):
                        f.write(chunk)
            im = Image.open(img_path)
            im.load()
            rows = []
            for gl in gt.lines:
                ln = seg_lines.get(gl.line_id)
                ref = _reference(gl.text, gt.source == "transcription")
                if ln is None or not ln.baseline:
                    continue
                if ref is None:
                    manifest["excluded_lines"] += 1
                    continue
                if not ref:
                    manifest["empty_lines"] += 1
                    continue
                rows.append((ln, ref))
            missing = [ln for ln, _ in rows if not ln.boundary]
            computed = {}
            if missing:
                polys = engine.compute_boundaries(im, [ln.baseline for ln in missing])
                computed = {ln.id: p for ln, p in zip(missing, polys)}
            P = "{%s}" % PAGE_NS
            root = etree.Element(P + "PcGts", nsmap={None: PAGE_NS})
            md = etree.SubElement(root, P + "Metadata")
            etree.SubElement(md, P + "Creator").text = creator
            etree.SubElement(md, P + "Created").text = _now().isoformat()
            etree.SubElement(md, P + "LastChange").text = _now().isoformat()
            img_ref = f"../images/{img_path.name}" if relative_images else str(img_path)
            pg = etree.SubElement(root, P + "Page", imageFilename=img_ref, imageWidth=str(im.width), imageHeight=str(im.height))
            tr = etree.SubElement(pg, P + "TextRegion", id="r1")
            etree.SubElement(tr, P + "Coords", points=f"0,0 {im.width - 1},0 {im.width - 1},{im.height - 1} 0,{im.height - 1}")
            n = chars = 0
            for i, (ln, ref) in enumerate(rows):
                poly = ln.boundary or computed.get(ln.id)
                if not poly:
                    manifest["excluded_lines"] += 1
                    continue
                tl = etree.SubElement(tr, P + "TextLine", id=f"l{i}")
                etree.SubElement(tl, P + "Coords", points=_poly_str(poly))
                etree.SubElement(tl, P + "Baseline", points=_poly_str(ln.baseline))
                etree.SubElement(etree.SubElement(tl, P + "TextEquiv"), P + "Unicode").text = ref
                n += 1
                chars += len(ref)
            data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
            path = work / split / f"{gt.id}.xml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files.append(str(path))
            entries.append({"gt_page_id": str(gt.id), "page_id": str(page.id), "lines": n, "chars": chars,
                            "image_sha256": asset.sha256, "page_xml_sha256": hashlib.sha256(data).hexdigest()})
            log(f"prepared {split} page", gt_page_id=str(gt.id), lines=n)
        (work / f"{split}.txt").write_text("\n".join(
            (str(Path(f).relative_to(work)) if relative_images else f) for f in files) + "\n")
        manifest["splits"][split] = {"pages": entries, "lines": sum(e["lines"] for e in entries),
                                     "chars": sum(e["chars"] for e in entries)}
    if tuple(splits) == ("train", "validation") and (not manifest["splits"]["train"]["lines"] or not manifest["splits"]["validation"]["lines"]):
        raise RuntimeError("no usable training or validation lines after preparation")
    manifest["test_split_used"] = "test" in splits
    return manifest


# -------------------------------------------------------------------------------- orchestration
class _Log:
    def __init__(self, db, job_id):
        self.db, self.job_id = db, job_id

    def __call__(self, message: str, level: str = "info", **extra):
        entry = {"ts": _now().isoformat(), "level": level, "message": message, **extra}
        self.db.execute(update(TrainingJob).where(TrainingJob.id == self.job_id)
                        .values(log=TrainingJob.log.op("||")(literal(json.loads(json.dumps([entry], default=str)), type_=JSONB))))
        self.db.commit()


def _set(db, job_id, **values):
    db.execute(update(TrainingJob).where(TrainingJob.id == job_id).values(**values))
    db.commit()


def run_training(db, job_id: uuid.UUID, storage, worker: str) -> TrainingJob:
    s = get_settings()
    job = db.get(TrainingJob, job_id)
    if job is None or job.status != TrainingStatus.QUEUED:
        return job
    log = _Log(db, job.id)
    work = (s.training_dir / str(job.id)).resolve()
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    _set(db, job.id, status=TrainingStatus.PREPARING, stage="preparing data", started_at=_now(), heartbeat_at=_now(),
         worker=worker, work_dir=str(work))
    try:
        manifest = prepare_data(db, job, storage, work, log)
        _set(db, job.id, data_manifest=manifest)
        log("training data prepared", train_lines=manifest["splits"]["train"]["lines"],
            validation_lines=manifest["splits"]["validation"]["lines"], excluded=manifest["excluded_lines"])
        base = db.get(ModelVersion, job.base_model_version_id) if job.base_model_version_id else None
        need, avail = estimated_training_memory(base), available_memory_bytes()
        if avail is not None and avail < need:
            log("available memory may be insufficient for training", level="warning",
                available_mb=avail // 2**20, estimated_need_mb=need // 2**20)
        cfg = {**job.config, "base_weights": base.file_path if base else None, "threads": s.training_threads}
        (work / "config.json").write_text(json.dumps(cfg, indent=2))
        if _status(db, job.id) == TrainingStatus.CANCELLED:
            raise _Cancelled()
        _set(db, job.id, status=TrainingStatus.TRAINING, stage="training", heartbeat_at=_now())
        with open(work / "runner.log", "ab") as runner_log:  # the child keeps its own descriptor
            proc = subprocess.Popen([sys.executable, "-m", "app.training.runner", str(work)],  # nosec B603 — fixed argv, server-generated path
                                    stdout=runner_log, stderr=subprocess.STDOUT,
                                    cwd=str(Path(__file__).resolve().parents[2]), start_new_session=True)
        _set(db, job.id, pid=proc.pid)
        log("training process started", pid=proc.pid, base=f"{base.model.slug} {base.version}" if base else "new model")
        done = _follow(db, job, proc, work, log)
        if done is None:
            raise _Cancelled()
        _set(db, job.id, status=TrainingStatus.REGISTERING, stage="registering model", progress=0.98,
             best_checkpoint=done["best_checkpoint"], checkpoints=_checkpoint_list(work, done))
        mv = register_output(db, job, base, Path(done["weights"]), done, manifest)
        log("model registered and validated by Kraken", model=f"{mv.model.slug} {mv.version}", sha256=mv.sha256)
        run_id = None
        if job.config.get("evaluate_test"):
            run_id = _start_test_evaluation(db, job, mv, log)
        _set(db, job.id, status=TrainingStatus.COMPLETED, stage=None, progress=1.0, completed_at=_now(),
             output_model_version_id=mv.id, evaluation_run_id=run_id, duration_s=time.time() - t0, pid=None)
        audit.record(db, "training.completed", actor_id=job.submitted_by_id, entity_type="training_job", entity_id=job.id,
                     project_id=job.project_id, details={"model_version_id": str(mv.id), "best": done["best_score"]})
        db.commit()
    except _Cancelled:
        _set(db, job.id, status=TrainingStatus.CANCELLED, stage=None, completed_at=_now(), duration_s=time.time() - t0, pid=None)
        log("training cancelled; no model was registered", level="warning")
    except Exception as e:  # noqa: BLE001 — every failure is reported on the job
        db.rollback()
        _set(db, job.id, status=TrainingStatus.FAILED, stage=None, completed_at=_now(), duration_s=time.time() - t0, pid=None,
             error={"code": getattr(e, "code", "training_failed"), "message": f"{type(e).__name__}: {e}"[:2000]})
        log("training failed", level="error", error=f"{type(e).__name__}: {e}"[:2000])
    db.expire_all()
    job = db.get(TrainingJob, job_id)
    db.commit()  # never leave the caller's session idle in a transaction
    return job


class _Cancelled(Exception):
    pass


def _status(db, job_id):
    return db.scalar(select(TrainingJob.status).where(TrainingJob.id == job_id))


def _follow(db, job: TrainingJob, proc: subprocess.Popen, work: Path, log) -> dict | None:
    """Relay runner events to the database until the process exits."""
    events = work / "events.jsonl"
    pos, done, error, epochs = 0, None, None, list(job.epochs or [])
    cancel_sent = None
    max_epochs = job.config["epochs"]
    while True:
        finished = proc.poll() is not None
        if events.exists():
            with open(events, encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev["type"]
                if t == "progress":
                    frac = (ev["epoch"] + ev["batch"] / max(ev["batches"] or 1, 1)) / max_epochs
                    _set(db, job.id, progress=round(0.05 + 0.9 * min(frac, 1.0), 4), current_epoch=ev["epoch"] + 1,
                         heartbeat_at=_now())
                elif t == "epoch":
                    epochs.append({k: ev.get(k) for k in ("epoch", "val_accuracy", "val_word_accuracy", "train_loss")}
                                  | {"at": _now().isoformat()})
                    _set(db, job.id, epochs=epochs, current_epoch=ev["epoch"] + 1, heartbeat_at=_now(),
                         checkpoints=_checkpoint_list(work, None))
                    log(f"epoch {ev['epoch'] + 1} validated", val_accuracy=ev.get("val_accuracy"),
                        val_word_accuracy=ev.get("val_word_accuracy"))
                elif t == "done":
                    done = ev
                elif t == "error":
                    error = ev
        if finished:
            break
        _set(db, job.id, heartbeat_at=_now())
        if cancel_sent is None and _status(db, job.id) == TrainingStatus.CANCELLED:
            (work / "CANCEL").touch()
            cancel_sent = time.time()
            log("cancellation requested; stopping at the next batch", level="warning")
        if cancel_sent and time.time() - cancel_sent > 120:
            os.killpg(proc.pid, signal.SIGKILL)  # the runner did not stop by itself
        time.sleep(2)
    if cancel_sent or (work / "CANCEL").exists():
        return None
    if error or proc.returncode != 0 or done is None:
        msg = (error or {}).get("message") or f"training process exited with code {proc.returncode}"
        if proc.returncode == -signal.SIGKILL and not error:
            msg = ("The training process was killed by the operating system (SIGKILL), most likely because the "
                   "machine ran out of memory. Free memory on the training host (other workers, browsers), run "
                   "the training worker on a dedicated host, or reduce the batch size.")
            exc = RuntimeError(msg)
            exc.code = "out_of_memory"  # type: ignore[attr-defined]
            raise exc
        raise RuntimeError(msg)
    return done


def available_memory_bytes() -> int | None:
    """Memory available to this process: the cgroup v2 limit if any, else MemAvailable."""
    try:
        cg = Path("/sys/fs/cgroup")
        mx, cur = (cg / "memory.max").read_text().strip(), (cg / "memory.current").read_text().strip()
        if mx != "max":
            return int(mx) - int(cur)
    except (OSError, ValueError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def estimated_training_memory(base: ModelVersion | None) -> int:
    """Measured on Kraken 7.1.1 CPU training: peak RSS ≈ 13 × the weights size (TraPrInq 160 MB → 2.1 GB)."""
    return max(1_500_000_000, 13 * (base.size_bytes or 0) if base else 0)


def _checkpoint_list(work: Path, done: dict | None) -> list[dict]:
    out = []
    for p in sorted((work / "checkpoints").glob("checkpoint_*.ckpt")):
        if p.name == "checkpoint_abort.ckpt":
            continue
        try:
            epoch, score = p.stem.split("_", 1)[1].split("-")
            out.append({"name": p.name, "epoch": int(epoch) + 1, "val_accuracy": float(score), "bytes": p.stat().st_size,
                        "deleted": False})
        except ValueError:
            continue
    return out


def next_version(db, model: RecognitionModel) -> str:
    n = len(model.versions) + 1
    while any(v.version == str(n) for v in model.versions):
        n += 1
    return str(n)


def register_output(db, job: TrainingJob, base: ModelVersion | None, weights: Path, done: dict, manifest: dict,
                    checkpoint: str | None = None) -> ModelVersion:
    """Register trained weights as a version of the project's institutional model, then validate them."""
    from app.registry.local import register_file, validate

    project = db.get(Project, job.project_id)
    slug = f"{slugify(job.output_name)}-{str(project.id)[:8]}"
    m = db.scalar(select(RecognitionModel).where(RecognitionModel.slug == slug))
    version = next_version(db, m) if m else "1"
    mv = register_file(db, src=weights, slug=slug, name=job.output_name, task=ModelTask.RECOGNITION, version=version,
                       source=ModelSource.TRAINED, file_name=f"{slug}-v{version}.safetensors", license_id=None,
                       unicode_normalization=job.config.get("normalization"),
                       description=f"Trained on the SCRIPTORIUM project “{project.title}”.")
    m = mv.model
    m.owner_project_id, m.owner_institution_id = project.id, project.institution_id
    mv.derived_from_id = base.id if base else None
    mv.training_job_id = job.id
    mv.provenance = {
        "kind": "fine-tuned" if base else "trained from scratch",
        "base_model": ({"name": base.model.name, "version": base.version, "doi": base.doi, "sha256": base.sha256,
                        "license": base.license_id, "model_version_id": str(base.id)} if base else None),
        "attribution_note": (f"Derived from {base.model.name} {base.version} (DOI {base.doi}, licence {base.license_id}); "
                             "the base model's licence terms apply to this derivative.") if base else None,
        "training_job_id": str(job.id), "checkpoint": checkpoint or done.get("best_checkpoint"),
        "validation_accuracy": done.get("best_score"), "validation_metric": "Kraken val_accuracy (character accuracy on the validation split)",
        "config": job.config, "data": manifest,
    }
    db.flush()
    validate(db, mv)
    if mv.status != ModelStatus.VALIDATED:
        raise RuntimeError(f"Kraken could not load the trained weights: {mv.install_log[-1] if mv.install_log else ''}")
    if m.active_version_id is None:
        m.active_version_id = mv.id
    db.commit()
    return mv


def _start_test_evaluation(db, job: TrainingJob, mv: ModelVersion, log) -> uuid.UUID | None:
    """Evaluate the new model on the TEST split (never seen in training) with the standard methodology."""
    from app.evaluation import metrics
    from app.models import EvaluationRun, Segmentation, User
    from app.services import jobs as jobsvc

    dataset = db.get(EvaluationDataset, job.dataset_id)
    pages = active_pages(db, dataset, "test")
    if not pages:
        log("no test pages: the trained model was not evaluated", level="warning")
        return None
    user = db.get(User, job.submitted_by_id)
    project = db.get(Project, job.project_id)
    norm = metrics.Normalization()
    run = EvaluationRun(id=uuid.uuid4(), dataset_id=dataset.id, model_version_id=mv.id, split="test",
                        methodology=norm.describe(), page_count=len(pages), batch_id=uuid.uuid4(), created_by_id=user.id)
    db.add(run)
    db.flush()
    created = [jobsvc.recognize_on_segmentation(db, user, project, db.get(Page, gt.page_id), db.get(Segmentation, gt.segmentation_id),
                                                mv, run.batch_id, {"evaluation": {"run_id": str(run.id), "gt_page_id": str(gt.id)}})
               for gt in pages]
    db.commit()
    for j in created:
        jobsvc.enqueue(j)
    log("evaluation on the test split started", run_id=str(run.id), pages=len(pages))
    return run.id


def recover_stale(db, stale_after: int = 300) -> list[str]:
    """Training jobs whose worker vanished (no heartbeat) are marked failed — never silently restarted."""
    from datetime import timedelta

    limit = _now() - timedelta(seconds=stale_after)
    ids = list(db.scalars(select(TrainingJob.id).where(
        TrainingJob.status.in_((TrainingStatus.PREPARING, TrainingStatus.TRAINING, TrainingStatus.REGISTERING)),
        TrainingJob.heartbeat_at < limit)))
    for i in ids:
        _set(db, i, status=TrainingStatus.FAILED, completed_at=_now(), pid=None,
             error={"code": "worker_lost", "message": "The training worker stopped responding; checkpoints are kept"})
    return [str(i) for i in ids]


def delete_checkpoint(job: TrainingJob, name: str) -> int:
    path = Path(job.work_dir or "") / "checkpoints" / name
    if not name.startswith("checkpoint_") or "/" in name or not path.is_file():
        raise ApiError(404, "not_found", "checkpoint not found")
    size = path.stat().st_size
    path.unlink()
    return size


def promote_checkpoint(db, job: TrainingJob, name: str) -> ModelVersion:
    """Convert a kept checkpoint to weights and register it as another model version."""
    from kraken.models.convert import convert_models

    path = Path(job.work_dir or "") / "checkpoints" / name
    if not path.is_file():
        raise ApiError(404, "not_found", "checkpoint not found")
    out = path.with_suffix(".safetensors")
    if not out.exists():
        convert_models([str(path)], out, weights_format="safetensors")
    ck = next((c for c in job.checkpoints if c["name"] == name), {})
    base = db.get(ModelVersion, job.base_model_version_id) if job.base_model_version_id else None
    return register_output(db, job, base, out, {"best_checkpoint": name, "best_score": ck.get("val_accuracy")},
                           job.data_manifest, checkpoint=name)


def remove_work_dir(job: TrainingJob) -> None:
    if job.work_dir and Path(job.work_dir).is_dir():
        shutil.rmtree(job.work_dir, ignore_errors=True)
