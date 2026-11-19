"""Model training, checkpoints, institutional models and publication packages (Phase 6, §14, §5.3)."""
from __future__ import annotations

import io
import json
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import __version__
from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.models import (
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    ModelPublication,
    ModelSource,
    ModelStatus,
    ModelTask,
    ModelVersion,
    RecognitionModel,
    TrainingJob,
    TrainingStatus,
)
from app.services import audit
from app.storage import get_storage
from app.training import service as tr

router = APIRouter(tags=["training"])
MAX_MODEL_BYTES = 2 * 1024 ** 3


class TrainingIn(BaseModel):
    dataset_id: uuid.UUID
    base_model_version_id: uuid.UUID | None = None  # None = train from scratch (default VGSL spec)
    output_name: str = Field(min_length=3, max_length=200)
    epochs: int = Field(10, ge=1, le=1000)
    quit: Literal["fixed", "early"] = "early"
    min_epochs: int = Field(0, ge=0, le=1000)
    lag: int = Field(5, ge=1, le=100)
    lrate: float = Field(1e-4, gt=0, le=0.1)
    batch_size: int = Field(1, ge=1, le=32)
    resize: Literal["union", "new", "fail"] = "union"
    normalization: Literal["NFC", "NFD", "NFKC", "NFKD"] | None = None
    augment: bool = False
    seed: int = 42
    keep_checkpoints: int = Field(3, ge=1, le=10)
    evaluate_test: bool = True


def _job_out(db, j: TrainingJob, detail: bool = False) -> dict:
    base = db.get(ModelVersion, j.base_model_version_id) if j.base_model_version_id else None
    out_mv = db.get(ModelVersion, j.output_model_version_id) if j.output_model_version_id else None
    ds = db.get(EvaluationDataset, j.dataset_id)
    o = {"id": str(j.id), "project_id": str(j.project_id), "dataset_id": str(j.dataset_id), "dataset": ds.name if ds else None,
         "base_model": f"{base.model.name} · {base.version}" if base else None,
         "base_model_version_id": str(base.id) if base else None, "output_name": j.output_name, "config": j.config,
         "status": j.status.value, "progress": j.progress, "stage": j.stage, "current_epoch": j.current_epoch,
         "epochs": j.epochs, "checkpoints": j.checkpoints, "best_checkpoint": j.best_checkpoint,
         "output_model_version_id": str(out_mv.id) if out_mv else None,
         "output_model": f"{out_mv.model.name} · {out_mv.version}" if out_mv else None,
         "evaluation_run_id": str(j.evaluation_run_id) if j.evaluation_run_id else None, "error": j.error,
         "submitted_at": j.submitted_at.isoformat(), "started_at": j.started_at.isoformat() if j.started_at else None,
         "completed_at": j.completed_at.isoformat() if j.completed_at else None, "duration_s": j.duration_s,
         "data_summary": {s: {"pages": len(v["pages"]), "lines": v["lines"], "chars": v["chars"]}
                          for s, v in (j.data_manifest or {}).get("splits", {}).items()}}
    if j.evaluation_run_id:
        rs = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == j.evaluation_run_id)))
        rc, ce = sum(r.ref_chars for r in rs), sum(r.char_errors for r in rs)
        rw, we = sum(r.ref_words for r in rs), sum(r.word_errors for r in rs)
        run = db.get(EvaluationRun, j.evaluation_run_id)
        o["test_evaluation"] = {"pages_scored": len(rs), "pages": run.page_count if run else None,
                                "cer": ce / rc if rc else None, "wer": we / rw if rw else None}
    if detail:
        o["data_manifest"], o["log"] = j.data_manifest, j.log[-300:]
    return o


def _job(db, user, job_id, perm) -> tuple[TrainingJob, object]:
    j = db.get(TrainingJob, job_id)
    if j is None:
        raise not_found("training job")
    project, _ = load_project(db, user, j.project_id, perm)
    return j, project


@router.get("/projects/{project_id}/training-jobs")
def list_jobs(project_id: uuid.UUID, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.VIEW)
    js = db.scalars(select(TrainingJob).where(TrainingJob.project_id == project.id).order_by(TrainingJob.submitted_at.desc()))
    return [_job_out(db, j) for j in js]


@router.post("/projects/{project_id}/training-jobs", status_code=201)
def create_job(project_id: uuid.UUID, body: TrainingIn, request: Request, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.TRAIN_MODELS)
    ds = db.get(EvaluationDataset, body.dataset_id)
    if ds is None:
        raise not_found("evaluation dataset")
    base = db.get(ModelVersion, body.base_model_version_id) if body.base_model_version_id else None
    if body.base_model_version_id and base is None:
        raise not_found("base model")
    cfg = body.model_dump(mode="json", exclude={"dataset_id", "base_model_version_id", "output_name"})
    if cfg["normalization"] is None and base is not None and base.unicode_normalization in ("NFC", "NFD", "NFKC", "NFKD"):
        cfg["normalization"] = base.unicode_normalization  # keep the base model's documented convention
    cfg["deterministic"] = True
    counts = tr.check_request(db, project, ds, base, cfg)
    j = TrainingJob(id=uuid.uuid4(), project_id=project.id, dataset_id=ds.id, base_model_version_id=base.id if base else None,
                    output_name=body.output_name, config=cfg, status=TrainingStatus.QUEUED, submitted_by_id=user.id,
                    epochs=[], checkpoints=[], log=[], data_manifest={})
    db.add(j)
    audit.record(db, "training.submitted", actor_id=user.id, request=request, entity_type="training_job", entity_id=j.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"dataset": ds.name, "splits": counts, "base": f"{base.model.slug} {base.version}" if base else None,
                          "config": cfg})
    db.commit()
    from app.worker.tasks import run_training

    run_training.apply_async(args=[str(j.id)], task_id=f"training-{j.id}")
    db.refresh(j)
    return _job_out(db, j)


@router.get("/training-jobs/{job_id}")
def get_job(job_id: uuid.UUID, user: CurrentUser, db: DB):
    j, _ = _job(db, user, job_id, P.VIEW)
    return _job_out(db, j, detail=True)


@router.post("/training-jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    j, project = _job(db, user, job_id, P.TRAIN_MODELS)
    if j.status in tr.TERMINAL:
        raise ApiError(409, "not_cancellable", f"The training job is already '{j.status.value}'")
    queued = j.status == TrainingStatus.QUEUED
    j.status = TrainingStatus.CANCELLED
    if queued:
        j.completed_at = datetime.now(timezone.utc)
    audit.record(db, "training.cancelled", actor_id=user.id, request=request, entity_type="training_job", entity_id=j.id,
                 project_id=project.id)
    db.commit()
    return _job_out(db, j)


@router.delete("/training-jobs/{job_id}/checkpoints/{name}")
def delete_checkpoint(job_id: uuid.UUID, name: str, request: Request, user: CurrentUser, db: DB):
    j, project = _job(db, user, job_id, P.TRAIN_MODELS)
    if j.status in tr.ACTIVE:
        raise ApiError(409, "training_active", "Checkpoints can be deleted once training has finished")
    freed = tr.delete_checkpoint(j, name)
    j.checkpoints = [{**c, "deleted": True} if c["name"] == name else c for c in j.checkpoints]
    audit.record(db, "training.checkpoint_deleted", actor_id=user.id, request=request, entity_type="training_job",
                 entity_id=j.id, project_id=project.id, details={"checkpoint": name, "bytes": freed})
    db.commit()
    return _job_out(db, j)


@router.post("/training-jobs/{job_id}/checkpoints/{name}/promote", status_code=202)
def promote_checkpoint(job_id: uuid.UUID, name: str, request: Request, user: CurrentUser, db: DB):
    j, project = _job(db, user, job_id, P.TRAIN_MODELS)
    if j.status != TrainingStatus.COMPLETED:
        raise ApiError(409, "training_not_completed", "Only checkpoints of a completed training job can be registered")
    if not any(c["name"] == name and not c.get("deleted") for c in j.checkpoints):
        raise not_found("checkpoint")
    audit.record(db, "training.checkpoint_promoted", actor_id=user.id, request=request, entity_type="training_job",
                 entity_id=j.id, project_id=project.id, details={"checkpoint": name})
    db.commit()
    from app.worker.tasks import promote_checkpoint as task

    r = task.apply_async(args=[str(j.id), name])
    return {"status": "queued", "task_id": r.id}


# ----------------------------------------------------------------------- institutional models
def _inst_out(db, mv: ModelVersion) -> dict:
    m = mv.model
    pubs = list(db.scalars(select(ModelPublication).where(ModelPublication.model_version_id == mv.id)
                           .order_by(ModelPublication.created_at.desc())))
    return {"id": str(mv.id), "model_id": str(m.id), "name": m.name, "slug": m.slug, "version": mv.version,
            "source": m.source.value, "status": mv.status.value, "sha256": mv.sha256, "size_bytes": mv.size_bytes,
            "license_id": mv.license_id, "unicode_normalization": mv.unicode_normalization,
            "kraken_version_validated": mv.kraken_version_validated,
            "validated_at": mv.validated_at.isoformat() if mv.validated_at else None,
            "created_at": mv.created_at.isoformat(), "is_active": m.active_version_id == mv.id,
            "owner_project_id": str(m.owner_project_id) if m.owner_project_id else None,
            "training_job_id": str(mv.training_job_id) if mv.training_job_id else None,
            "provenance": {k: v for k, v in (mv.provenance or {}).items() if k != "data"},
            "description": m.description, "install_log": mv.install_log[-20:],
            "publications": [_pub_out(p) for p in pubs]}


def _pub_out(p: ModelPublication) -> dict:
    return {"id": str(p.id), "title": p.title, "authors": p.authors, "license_id": p.license_id, "keywords": p.keywords,
            "description": p.description, "approved_at": p.approved_at.isoformat() if p.approved_at else None,
            "approval_statement": p.approval_statement, "package_sha256": p.package_sha256,
            "package_bytes": p.package_bytes, "published_doi": p.published_doi, "created_at": p.created_at.isoformat()}


def _inst_mv(db, user, mv_id: uuid.UUID, perm: P):
    mv = db.get(ModelVersion, mv_id)
    if mv is None or mv.model.owner_project_id is None:
        raise not_found("institutional model")
    project, perms = load_project(db, user, mv.model.owner_project_id, perm)
    return mv, project, perms


@router.get("/projects/{project_id}/institutional-models")
def list_institutional(project_id: uuid.UUID, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.VIEW)
    mvs = db.scalars(select(ModelVersion).join(RecognitionModel, RecognitionModel.id == ModelVersion.model_id)
                     .where(RecognitionModel.owner_project_id == project.id).order_by(RecognitionModel.name, ModelVersion.created_at))
    return [_inst_out(db, mv) for mv in mvs]


@router.post("/projects/{project_id}/institutional-models", status_code=201)
def upload_model(project_id: uuid.UUID, request: Request, user: CurrentUser, db: DB,
                       name: str = Form(..., min_length=3, max_length=200), version: str = Form(..., min_length=1, max_length=60),
                       license_id: str | None = Form(None), unicode_normalization: str | None = Form(None),
                       description: str | None = Form(None), file: UploadFile = File(...)):
    """Manual registration of a Kraken model file (.mlmodel CoreML or .safetensors weights).
    The file is stored, checksummed and then loaded by Kraken on the models worker before it can be used.
    (Synchronous handler: disk writes and the malware scan run in the threadpool, not on the event loop.)"""
    from app.registry.local import ModelInstallError, register_file

    project, _ = load_project(db, user, project_id, P.TRAIN_MODELS)
    fname = Path(file.filename or "model").name
    if not fname.endswith((".mlmodel", ".safetensors")):
        raise ApiError(422, "unsupported_model_file", "Upload a Kraken model file (.mlmodel or .safetensors)")
    if unicode_normalization not in (None, "", "NFC", "NFD", "NFKC", "NFKD"):
        raise ApiError(422, "invalid_normalization", "unicode_normalization must be NFC, NFD, NFKC or NFKD")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / fname
        size = 0
        with open(tmp, "wb") as f:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_MODEL_BYTES:
                    raise ApiError(413, "file_too_large", "Model files are limited to 2 GB")
                f.write(chunk)
        from app.services import malware

        with open(tmp, "rb") as f:
            malware.scan_or_raise(f, fname, kind="model_file", actor_id=user.id, request=request,
                                  project_id=project.id, institution_id=project.institution_id)
        slug = f"{tr.slugify(name)}-{str(project.id)[:8]}"
        try:
            mv = register_file(db, src=tmp, slug=slug, name=name, task=ModelTask.RECOGNITION, version=version,
                               source=ModelSource.MANUAL, license_id=license_id or None,
                               unicode_normalization=unicode_normalization or None, description=description)
        except ModelInstallError as e:
            raise ApiError(409, e.code, e.message) from e
    mv.model.owner_project_id, mv.model.owner_institution_id = project.id, project.institution_id
    mv.provenance = {"kind": "uploaded", "uploaded_by": user.full_name, "original_filename": fname}
    audit.record(db, "model.uploaded", actor_id=user.id, request=request, entity_type="model_version", entity_id=mv.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"name": name, "version": version, "sha256": mv.sha256, "bytes": mv.size_bytes})
    db.commit()
    from app.worker.tasks import validate_model

    validate_model.apply_async(args=[str(mv.id)])
    db.refresh(mv)
    return _inst_out(db, mv)


@router.get("/institutional-models/{mv_id}")
def get_institutional(mv_id: uuid.UUID, user: CurrentUser, db: DB):
    mv, _, _ = _inst_mv(db, user, mv_id, P.VIEW)
    return _inst_out(db, mv)


@router.post("/institutional-models/{mv_id}/activate")
def activate(mv_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    mv, project, _ = _inst_mv(db, user, mv_id, P.TRAIN_MODELS)
    if mv.status != ModelStatus.VALIDATED:
        raise ApiError(409, "model_not_validated", "Only a validated version can be made active")
    mv.model.active_version_id = mv.id
    audit.record(db, "model.activated", actor_id=user.id, request=request, entity_type="model_version", entity_id=mv.id,
                 project_id=project.id)
    db.commit()
    return _inst_out(db, mv)


@router.get("/institutional-models/{mv_id}/download")
def download_weights(mv_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    """Weights of a model owned by this project, for its members (export permission)."""
    mv, project, _ = _inst_mv(db, user, mv_id, P.EXPORT)
    audit.record(db, "model.downloaded", actor_id=user.id, request=request, entity_type="model_version", entity_id=mv.id,
                 project_id=project.id, details={"sha256": mv.sha256})
    db.commit()

    def it():
        with open(mv.file_path, "rb") as f:
            while b := f.read(1 << 20):
                yield b

    return StreamingResponse(it(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{mv.file_name}"',
                                      "X-Content-SHA256": mv.sha256, "Content-Length": str(mv.size_bytes)})


# --------------------------------------------------------------------------- publication
class Author(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    affiliation: str | None = Field(default=None, max_length=300)
    orcid: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


class PublicationIn(BaseModel):
    title: str = Field(min_length=5, max_length=300)
    description: str = Field(min_length=20, max_length=20000)
    authors: list[Author] = Field(min_length=1, max_length=50)
    license_id: str = Field(min_length=2, max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=30)


class ApprovalIn(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)


class PublishedIn(BaseModel):
    doi: str = Field(pattern=r"^10\.\d{4,9}/\S+$")


@router.post("/institutional-models/{mv_id}/publications", status_code=201)
def create_publication(mv_id: uuid.UUID, body: PublicationIn, request: Request, user: CurrentUser, db: DB):
    mv, project, _ = _inst_mv(db, user, mv_id, P.TRAIN_MODELS)
    if mv.status != ModelStatus.VALIDATED:
        raise ApiError(409, "model_not_validated", "Only a validated model can be prepared for publication")
    base = db.get(ModelVersion, mv.derived_from_id) if mv.derived_from_id else None
    if base and base.license_id and base.license_id.lower().startswith("cc-by") and not body.license_id.lower().startswith("cc-by"):
        raise ApiError(422, "licence_incompatible", f"This model is derived from {base.model.name} ({base.license_id}); "
                       "its licence requires attribution — choose a CC-BY licence")
    p = ModelPublication(id=uuid.uuid4(), model_version_id=mv.id, title=body.title, description=body.description,
                         authors=[a.model_dump() for a in body.authors], license_id=body.license_id, keywords=body.keywords,
                         created_by_id=user.id)
    db.add(p)
    audit.record(db, "model.publication_drafted", actor_id=user.id, request=request, entity_type="model_publication",
                 entity_id=p.id, project_id=project.id)
    db.commit()
    return _pub_out(p)


def model_card(mv: ModelVersion, p: ModelPublication, test_eval: dict | None, languages: list[str]) -> str:
    """htrmopo v1 model card (YAML front matter + Markdown), validated before packaging."""
    import yaml

    prov = mv.provenance or {}
    base = prov.get("base_model")
    meta = {
        "authors": [{k: v for k, v in a.items() if v} for a in p.authors],
        "summary": p.title,
        "license": p.license_id,
        "software_name": "kraken",
        "software_hints": [f"kraken_pytorch>={mv.kraken_version_validated or '7.1.1'}"],
        "language": languages or ["und"], "script": ["Latn"],
        "model_type": ["recognition"],
        "metrics": {"cer": round(100 * test_eval["cer"], 2)} if test_eval and test_eval.get("cer") is not None else {},
        "keywords": p.keywords,
    }
    meta = {k: v for k, v in meta.items() if v not in (None, [], {})}
    if base:
        meta["base_model"] = [f"https://doi.org/{base['doi']}"] if base.get("doi") else []
    data = prov.get("data") or {}
    body = [f"# {p.title}", "", p.description, "", "## Training", "",
            f"* Kind: {prov.get('kind', 'uploaded')}"]
    if base:
        body.append(f"* Base model: {base['name']} {base['version']} (DOI {base.get('doi')}, licence {base.get('license')})")
    if data:
        sp = data.get("splits", {})
        body += [f"* Training lines: {sp.get('train', {}).get('lines')}; validation lines: {sp.get('validation', {}).get('lines')}",
                 f"* Unicode normalisation: {data.get('normalization')}; seed {data.get('seed')}",
                 f"* Software: {json.dumps(data.get('software', {}))}"]
    if prov.get("validation_accuracy") is not None:
        body.append(f"* Best validation character accuracy (Kraken, validation split): {prov['validation_accuracy']:.4f}")
    if test_eval and test_eval.get("cer") is not None:
        body.append(f"* Test split CER (SCRIPTORIUM methodology scriptorium-metrics/1): {100 * test_eval['cer']:.2f} % "
                    f"on {test_eval['pages']} page(s)")
    body += ["", f"Prepared with SCRIPTORIUM {__version__}."]
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + "\n".join(body) + "\n"


def zenodo_metadata(p: ModelPublication, mv: ModelVersion) -> dict:
    prov = mv.provenance or {}
    base = prov.get("base_model")
    md = {"metadata": {
        "upload_type": "other", "title": p.title, "description": p.description,
        "creators": [{"name": a["name"], **({"affiliation": a["affiliation"]} if a.get("affiliation") else {}),
                      **({"orcid": a["orcid"]} if a.get("orcid") else {})} for a in p.authors],
        "license": p.license_id, "keywords": ["kraken_pytorch", "HTR", *p.keywords], "version": mv.version,
        "related_identifiers": ([{"identifier": base["doi"], "relation": "isDerivedFrom", "scheme": "doi"}]
                                if base and base.get("doi") else []),
    }}
    return md


@router.post("/model-publications/{pub_id}/approve")
def approve_publication(pub_id: uuid.UUID, body: ApprovalIn, request: Request, user: CurrentUser, db: DB):
    """The PI confirms that every listed author agreed; the package is then generated."""
    p = db.get(ModelPublication, pub_id)
    if p is None:
        raise not_found("publication")
    mv, project, _ = _inst_mv(db, user, p.model_version_id, P.MANAGE_MEMBERS)  # PI / project manager only
    if p.approved_at:
        raise ApiError(409, "already_approved", "This publication was already approved")
    test_eval = None
    if mv.training_job_id:
        tj = db.get(TrainingJob, mv.training_job_id)
        if tj and tj.output_model_version_id == mv.id and tj.evaluation_run_id:
            test_eval = _job_out(db, tj).get("test_evaluation")
    card = model_card(mv, p, test_eval, list(project.languages or []))
    from app.registry.zenodo import validate_card_text

    parsed = validate_card_text(card)
    if not parsed.get("valid"):
        raise ApiError(500, "model_card_invalid", "The generated model card does not validate", details=parsed)
    zen = zenodo_metadata(p, mv)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(mv.file_path, mv.file_name)
        z.writestr("README.md", card)
        z.writestr("zenodo.json", json.dumps(zen, ensure_ascii=False, indent=2))
        z.writestr("PROVENANCE.json", json.dumps({**(mv.provenance or {}), "sha256": mv.sha256,
                                                  "approved_by": user.full_name, "approval_statement": body.statement},
                                                 ensure_ascii=False, indent=2, default=str))
        z.writestr("PUBLISHING.txt", "Publish with `ketos publish` or the Zenodo web interface using zenodo.json.\n"
                                     "SCRIPTORIUM never uploads to Zenodo by itself.\n")
    data = buf.getvalue()
    obj = get_storage().put_stream("publications", io.BytesIO(data))
    p.package_key, p.package_sha256, p.package_bytes = obj.key, obj.sha256, obj.size
    p.approved_by_id, p.approved_at, p.approval_statement = user.id, datetime.now(timezone.utc), body.statement
    audit.record(db, "model.publication_approved", actor_id=user.id, request=request, entity_type="model_publication",
                 entity_id=p.id, project_id=project.id, details={"sha256": obj.sha256, "card_format": parsed.get("format")})
    db.commit()
    out = _pub_out(p)
    out["model_card_validation"] = {"format": parsed.get("format"), "valid": parsed.get("valid"), "errors": parsed.get("errors")}
    return out


@router.get("/model-publications/{pub_id}/package")
def download_package(pub_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    p = db.get(ModelPublication, pub_id)
    if p is None or not p.package_key:
        raise not_found("publication package")
    mv, project, _ = _inst_mv(db, user, p.model_version_id, P.EXPORT)
    audit.record(db, "model.publication_downloaded", actor_id=user.id, request=request, entity_type="model_publication",
                 entity_id=p.id, project_id=project.id)
    db.commit()
    return StreamingResponse(get_storage().iter_bytes(p.package_key), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{mv.model.slug}-v{mv.version}-zenodo-package.zip"',
                                      "X-Content-SHA256": p.package_sha256})


@router.post("/model-publications/{pub_id}/published")
def record_published(pub_id: uuid.UUID, body: PublishedIn, request: Request, user: CurrentUser, db: DB):
    """Record the DOI after a person published the approved package on Zenodo."""
    p = db.get(ModelPublication, pub_id)
    if p is None:
        raise not_found("publication")
    mv, project, _ = _inst_mv(db, user, p.model_version_id, P.MANAGE_MEMBERS)
    if not p.approved_at:
        raise ApiError(409, "not_approved", "The publication must be approved first")
    p.published_doi = body.doi
    mv.doi = body.doi
    audit.record(db, "model.published", actor_id=user.id, request=request, entity_type="model_publication", entity_id=p.id,
                 project_id=project.id, details={"doi": body.doi})
    db.commit()
    return _pub_out(p)

