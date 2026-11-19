"""Model registry API: Zenodo preview/discovery/installation (platform administrators),
model catalogue and recommendation (all authenticated users)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.api.deps import DB, CurrentUser, PlatformAdmin
from app.api.recognition import mv_out
from app.core.errors import ApiError, not_found
from app.models import ModelStatus, ModelVersion, RecognitionModel
from app.registry import manager
from app.registry.manager import RegistryError
from app.registry.zenodo import ZenodoError
from app.schemas import _check_langs
from app.schemas_recognition import ModelVersionDetailOut
from app.services import audit

router = APIRouter(tags=["model registry"])


def _err(e: Exception) -> ApiError:
    if isinstance(e, ZenodoError):
        status = {"record_not_found": 404, "invalid_identifier": 422, "zenodo_unreachable": 502}.get(e.code, 502)
        return ApiError(status, e.code, e.message, details=e.details)
    if not isinstance(e, RegistryError):
        raise TypeError(f"unexpected error type {type(e).__name__}") from e
    status = {"license_not_accepted": 422, "already_installed": 409, "incompatible": 422, "no_model_file": 422,
              "ambiguous_file": 422, "not_validated": 409, "wrong_model": 422}.get(e.code, 400)
    return ApiError(status, e.code, e.message, details=e.details)


class InstallIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=300, description="Zenodo DOI, record URL or record id")
    file_key: str | None = None
    accept_license: bool = False
    family: str | None = Field(default=None, max_length=60)


class ActiveIn(BaseModel):
    version_id: uuid.UUID


class CurationIn(BaseModel):
    languages: list[str] | None = None
    period_start: int | None = Field(default=None, ge=0, le=2100)
    period_end: int | None = Field(default=None, ge=0, le=2100)
    script_type: Literal["manuscript", "print", "mixed"] | None = None
    transcription_convention: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    basis: str | None = Field(default=None, max_length=1000, description="Where the curated facts come from")

    @field_validator("languages")
    @classmethod
    def _l(cls, v):
        return _check_langs(v) if v is not None else v


def _model_out(db, m: RecognitionModel) -> dict:
    return {
        "id": str(m.id), "slug": m.slug, "name": m.name, "task": m.task.value, "source": m.source.value,
        "family": m.family, "concept_doi": m.concept_doi, "concept_record_id": m.concept_record_id,
        "description": m.description, "source_languages": m.source_languages, "source_scripts": m.source_scripts,
        "curated": m.curated, "active_version_id": str(m.active_version_id) if m.active_version_id else None,
        "latest_record_id": m.latest_record_id, "latest_version_label": m.latest_version_label,
        "update_available": m.update_available,
        "last_update_check_at": m.last_update_check_at.isoformat() if m.last_update_check_at else None,
        "versions": [mv_out(v).model_dump(mode="json") | {
            "record_id": v.record_id, "publication_date": v.publication_date, "bytes_downloaded": v.bytes_downloaded,
            "is_active": v.id == m.active_version_id,
            "source_reported_accuracy": (v.source_metadata or {}).get("source_reported_accuracy"),
            "model_card_format": v.model_card_format,
        } for v in sorted(m.versions, key=lambda v: v.created_at, reverse=True)],
    }


# ---------------------------------------------------------------- public catalogue
@router.get("/model-registry/models")
def registry_models(_: CurrentUser, db: DB):
    return [_model_out(db, m) for m in manager.models_with_versions(db)]


@router.get("/model-registry/recommend")
def recommend(_: CurrentUser, db: DB, languages: list[str] = Query(default=[]),
              script_type: Literal["manuscript", "print", "mixed"] | None = None,
              period_start: int | None = None, period_end: int | None = None):
    langs = _check_langs([x for v in languages for x in v.split(",") if x])
    return {"criteria": {"languages": langs, "script_type": script_type, "period_start": period_start, "period_end": period_end},
            "method": "Rank by declared attributes only: +1 per match, −2 per explicit mismatch. "
                      "Evidence is either the source (Zenodo record / model card) or administrator curation.",
            "items": manager.recommend(db, languages=langs, script_type=script_type,
                                       period_start=period_start, period_end=period_end)}


# ---------------------------------------------------------------- administration
@router.get("/admin/model-registry/catalog")
def catalog(_: PlatformAdmin):
    return manager.CATALOG


@router.get("/admin/model-registry/preview")
def preview(identifier: str, _: PlatformAdmin, db: DB):
    try:
        return manager.preview(db, identifier)
    except (ZenodoError, RegistryError) as e:
        raise _err(e)


@router.get("/admin/model-registry/discover")
def discover(_: PlatformAdmin, q: str = Query(min_length=2, max_length=200), page: int = Query(1, ge=1, le=40)):
    try:
        return manager.discover(q, page=page)
    except ZenodoError as e:
        raise _err(e)


@router.post("/admin/model-registry/install", response_model=ModelVersionDetailOut, status_code=202)
def install(body: InstallIn, request: Request, admin: PlatformAdmin, db: DB):
    try:
        mv = manager.start_install(db, user_id=admin.id, identifier=body.identifier, file_key=body.file_key,
                                   accept_license=body.accept_license, family=body.family)
    except (ZenodoError, RegistryError) as e:
        db.rollback()
        raise _err(e)
    audit.record(db, "model.install_requested", actor_id=admin.id, request=request, entity_type="model_version",
                 entity_id=mv.id, details={"record_id": mv.record_id, "file": mv.file_name, "license": mv.license_id,
                                           "license_accepted": True})
    db.commit()
    from app.worker.tasks import install_model

    install_model.delay(str(mv.id))
    db.refresh(mv)
    return mv_out(mv, detail=True)


@router.post("/admin/model-versions/{version_id}/retry", response_model=ModelVersionDetailOut, status_code=202)
def retry_install(version_id: uuid.UUID, request: Request, admin: PlatformAdmin, db: DB):
    mv = db.get(ModelVersion, version_id)
    if mv is None:
        raise not_found("Model version")
    if mv.status != ModelStatus.FAILED or not mv.record_id:
        raise ApiError(409, "not_retryable", "Only failed Zenodo installations can be retried")
    try:
        mv = manager.start_install(db, user_id=admin.id, identifier=mv.record_id, file_key=mv.file_name, accept_license=True)
    except (ZenodoError, RegistryError) as e:
        db.rollback()
        raise _err(e)
    audit.record(db, "model.install_retried", actor_id=admin.id, request=request, entity_type="model_version", entity_id=mv.id)
    db.commit()
    from app.worker.tasks import install_model

    install_model.delay(str(mv.id))
    db.refresh(mv)
    return mv_out(mv, detail=True)


@router.post("/admin/model-versions/{version_id}/revalidate", response_model=ModelVersionDetailOut)
def revalidate(version_id: uuid.UUID, request: Request, admin: PlatformAdmin, db: DB):
    mv = db.get(ModelVersion, version_id)
    if mv is None:
        raise not_found("Model version")
    if mv.status not in (ModelStatus.VALIDATED, ModelStatus.INSTALLED, ModelStatus.INCOMPATIBLE) or not mv.file_path:
        raise ApiError(409, "not_installed", "The model file is not installed")
    from app.worker.tasks import validate_model

    validate_model.delay(str(mv.id))
    audit.record(db, "model.revalidate", actor_id=admin.id, request=request, entity_type="model_version", entity_id=mv.id)
    db.commit()
    db.refresh(mv)
    return mv_out(mv, detail=True)


def _model(db, model_id: uuid.UUID) -> RecognitionModel:
    m = db.get(RecognitionModel, model_id)
    if m is None:
        raise not_found("Model")
    return m


@router.post("/admin/models/{model_id}/active-version")
def set_active(model_id: uuid.UUID, body: ActiveIn, request: Request, admin: PlatformAdmin, db: DB):
    """Pin a version / roll back to an earlier validated version."""
    m = _model(db, model_id)
    v = db.get(ModelVersion, body.version_id)
    if v is None:
        raise not_found("Model version")
    previous = m.active_version_id
    try:
        manager.set_active_version(db, m, v)
    except RegistryError as e:
        raise _err(e)
    audit.record(db, "model.activate_version", actor_id=admin.id, request=request, entity_type="model", entity_id=m.id,
                 details={"version": v.version, "version_id": str(v.id), "previous_version_id": str(previous) if previous else None})
    db.commit()
    return _model_out(db, m)


@router.post("/admin/models/{model_id}/check-updates")
def check_updates(model_id: uuid.UUID, admin: PlatformAdmin, db: DB):
    m = _model(db, model_id)
    try:
        res = manager.check_updates(db, m)
    except ZenodoError as e:
        raise _err(e)
    db.commit()
    return res


@router.patch("/admin/models/{model_id}/curation")
def curate(model_id: uuid.UUID, body: CurationIn, request: Request, admin: PlatformAdmin, db: DB):
    m = _model(db, model_id)
    changes = body.model_dump(exclude_unset=True)
    m.curated = {**(m.curated or {}), **changes, "curated_by": admin.email, "curated_at": manager._now().isoformat()}
    audit.record(db, "model.curate", actor_id=admin.id, request=request, entity_type="model", entity_id=m.id, details=changes)
    db.commit()
    return _model_out(db, m)


@router.get("/admin/model-versions/{version_id}", response_model=ModelVersionDetailOut)
def version_detail(version_id: uuid.UUID, _: PlatformAdmin, db: DB):
    mv = db.get(ModelVersion, version_id)
    if mv is None:
        raise not_found("Model version")
    return mv_out(mv, detail=True)
