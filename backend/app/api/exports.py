"""Exports (master prompt §13) and provenance records (§19)."""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.exports import batch
from app.exports import context as ctx
from app.exports import formats as fm
from app.exports import validate as val
from app.models import Collection, Document, Export, Page, Recognition, Transcription
from app.services import audit
from app.storage import get_storage

router = APIRouter(tags=["exports"])
Fmt = Literal["txt", "json", "alto", "page", "hocr", "tei", "docx", "pdf"]
Layer = Literal["htr", "corrected", "expanded", "reading", "normalized", "markup"]


def check_layer(fmt: str, layer: str) -> None:
    if layer == "markup" and fmt not in fm.MARKUP_LAYERS_OK:
        raise ApiError(422, "layer_not_supported", "The 'markup' layer (SCRIPTORIUM notation) is available in TXT and JSON only; "
                       "TEI maps the markup to TEI elements from the 'corrected' layer")
    if fmt == "tei" and layer not in ("corrected", "htr"):
        raise ApiError(422, "layer_not_supported", "TEI export encodes the corrected text with its editorial markup "
                       "(layer 'corrected') or the HTR output (layer 'htr'); expansions and normalisation are "
                       "controlled by the TEI mapping options")


def _page_project(db, user, page_id: uuid.UUID, perm: P):
    page = db.get(Page, page_id)
    doc = db.get(Document, page.document_id)
    project, _ = load_project(db, user, db.get(Collection, doc.collection_id).project_id, perm)
    return page, doc, project


def _ascii(name: str) -> str:
    return batch.slug(name, 80)


def _file_response(data: bytes, fmt: str, base: str, validation: dict | None) -> Response:
    media, ext = fm.FORMATS[fmt]
    name = f"{_ascii(base)}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if validation is not None:
        headers["X-Scriptorium-Validation"] = json.dumps(
            {"valid": validation["valid"], "schema": validation["schema"], "method": validation["method"],
             "errors": validation["errors"][:5]}, ensure_ascii=True)
        headers["Access-Control-Expose-Headers"] = "X-Scriptorium-Validation, Content-Disposition"
    return Response(content=data, media_type=media, headers=headers)


def _tei_mapping(abbreviations: str, cert: bool, facsimile: bool, normalized: bool, notes: bool) -> fm.TeiMapping:
    if abbreviations not in ("choice", "abbr", "expan"):
        raise ApiError(422, "invalid_mapping", "tei_abbreviations must be choice, abbr or expan")
    return fm.TeiMapping(abbreviations=abbreviations, uncertain_cert=cert, include_facsimile=facsimile,
                         include_normalized=normalized, include_notes=notes)


def _export_one(db, user, request, page, doc, project, fmt, layer, tx, rec, at_revision, validate, tei):
    check_layer(fmt, layer)
    try:
        pc = ctx.page_context(db, page, layer, transcription=tx, recognition=rec, at_revision=at_revision)
    except ctx.ExportError as e:
        raise ApiError(422, e.code, e.message, details=e.details) from e
    options = {"layer": layer, "format": fmt, "at_revision": at_revision, **({"tei": tei.model_dump()} if fmt == "tei" else {})}
    dc = ctx.document_context(db, doc, [pc], options, user)
    data = fm.build(fmt, dc, pc, tei)
    validation = val.validate(fmt, data) if validate and fmt in batch.XML_VALIDATED else None
    audit.record(db, "export.downloaded", actor_id=user.id, request=request, entity_type="page", entity_id=page.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"format": fmt, "layer": layer, "transcription_id": str(tx.id) if tx else None,
                          "recognition_id": str(rec.id) if rec else None, "revision": pc.text_source,
                          "valid": validation["valid"] if validation else None})
    db.commit()
    base = f"{doc.title}-p{page.sequence}-{layer}"
    return _file_response(data, fmt, base, validation)


@router.get("/transcriptions/{transcription_id}/export")
def export_transcription(transcription_id: uuid.UUID, request: Request, user: CurrentUser, db: DB,
                         format: Fmt = "tei", layer: Layer = "corrected", at_revision: int | None = None,
                         validate: bool = True, tei_abbreviations: str = "choice", tei_cert: bool = False,
                         tei_facsimile: bool = True, tei_normalized: bool = False, tei_notes: bool = True):
    tx = db.get(Transcription, transcription_id)
    if tx is None:
        raise not_found("transcription")
    page, doc, project = _page_project(db, user, tx.page_id, P.EXPORT)
    rec = db.get(Recognition, tx.base_recognition_id) if tx.base_recognition_id else None
    return _export_one(db, user, request, page, doc, project, format, layer, tx, rec, at_revision, validate,
                       _tei_mapping(tei_abbreviations, tei_cert, tei_facsimile, tei_normalized, tei_notes))


@router.get("/recognitions/{recognition_id}/export")
def export_recognition(recognition_id: uuid.UUID, request: Request, user: CurrentUser, db: DB,
                       format: Fmt = "page", validate: bool = True):
    rec = db.get(Recognition, recognition_id)
    if rec is None:
        raise not_found("recognition")
    page, doc, project = _page_project(db, user, rec.page_id, P.EXPORT)
    return _export_one(db, user, request, page, doc, project, format, "htr", None, rec, None, validate, fm.TeiMapping())


@router.get("/transcriptions/{transcription_id}/provenance")
def provenance(transcription_id: uuid.UUID, user: CurrentUser, db: DB, at_revision: int | None = None):
    """Full traceability record: source document and page, image checksums, segmentation, model
    (Zenodo DOI, checksum, licence), Kraken version, processing configuration, editorial history."""
    tx = db.get(Transcription, transcription_id)
    if tx is None:
        raise not_found("transcription")
    page, doc, project = _page_project(db, user, tx.page_id, P.VIEW)
    rec = db.get(Recognition, tx.base_recognition_id) if tx.base_recognition_id else None
    pc = ctx.page_context(db, page, "corrected", transcription=tx, recognition=rec, at_revision=at_revision)
    col = db.get(Collection, doc.collection_id)
    origins = sorted({ln.origin_model for ln in pc.lines if ln.origin_model})
    return {"document": {"id": str(doc.id), **ctx.document_metadata(doc, col)},
            "project": {"id": str(project.id), "title": project.title},
            "page": {"id": pc.id, "sequence": pc.sequence, "label": pc.label, "image": pc.image},
            "segmentation": pc.segmentation, "recognition": pc.recognition, "transcription": pc.transcription,
            "combined_from": origins, "quality": pc.quality}


# ------------------------------------------------------------------------------------- batch exports
class ExportIn(BaseModel):
    scope: Literal["document", "collection"]
    scope_id: uuid.UUID
    formats: list[Fmt] = Field(min_length=1, max_length=8)
    layer: Layer = "corrected"
    include_uncorrected: bool = True
    tei: fm.TeiMapping = Field(default_factory=fm.TeiMapping)


def _export_out(e: Export) -> dict:
    return {"id": str(e.id), "project_id": str(e.project_id), "scope": e.scope, "scope_id": str(e.scope_id),
            "formats": e.formats, "options": e.options, "status": e.status, "size_bytes": e.size_bytes,
            "sha256": e.sha256, "summary": e.summary, "error": {k: v for k, v in (e.error or {}).items() if k != "trace"} or None,
            "created_at": e.created_at.isoformat(), "completed_at": e.completed_at.isoformat() if e.completed_at else None}


@router.post("/projects/{project_id}/exports", status_code=201)
def create_export(project_id: uuid.UUID, body: ExportIn, request: Request, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.EXPORT)
    for f in body.formats:
        check_layer(f, body.layer)
    if body.scope == "document":
        d = db.get(Document, body.scope_id)
        ok = d is not None and db.get(Collection, d.collection_id).project_id == project.id
        title = d.title if ok else None
    else:
        c = db.get(Collection, body.scope_id)
        ok = c is not None and c.project_id == project.id
        title = c.title if ok else None
    if not ok:
        raise not_found(body.scope)
    e = Export(id=uuid.uuid4(), project_id=project.id, scope=body.scope, scope_id=body.scope_id,
               formats=list(dict.fromkeys(body.formats)), status="queued", created_by_id=user.id,
               options={"layer": body.layer, "include_uncorrected": body.include_uncorrected, "tei": body.tei.model_dump(),
                        "title": title})
    db.add(e)
    audit.record(db, "export.requested", actor_id=user.id, request=request, entity_type="export", entity_id=e.id,
                 project_id=project.id, institution_id=project.institution_id,
                 details={"scope": body.scope, "scope_id": str(body.scope_id), "formats": e.formats, "layer": body.layer})
    db.commit()
    from app.worker.tasks import build_export

    build_export.apply_async(args=[str(e.id)], task_id=f"export-{e.id}")
    db.refresh(e)
    return _export_out(e)


@router.get("/projects/{project_id}/exports")
def list_exports(project_id: uuid.UUID, user: CurrentUser, db: DB, limit: int = Query(50, le=200)):
    project, _ = load_project(db, user, project_id, P.VIEW)
    es = db.scalars(select(Export).where(Export.project_id == project.id).order_by(Export.created_at.desc()).limit(limit))
    return [_export_out(e) for e in es]


def _export(db, user, export_id, perm):
    e = db.get(Export, export_id)
    if e is None:
        raise not_found("export")
    load_project(db, user, e.project_id, perm)
    return e


@router.get("/exports/{export_id}")
def get_export(export_id: uuid.UUID, user: CurrentUser, db: DB):
    return _export_out(_export(db, user, export_id, P.VIEW))


@router.get("/exports/{export_id}/download")
def download_export(export_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    e = _export(db, user, export_id, P.EXPORT)
    if e.status == "expired":
        raise ApiError(410, "export_expired", "This export archive was removed under the retention policy; request a new export",
                       details={"expired_at": (e.summary or {}).get("expired_at")})
    if e.status != "completed" or not e.storage_key:
        raise ApiError(409, "export_not_ready", f"The export is '{e.status}'")
    audit.record(db, "export.downloaded", actor_id=user.id, request=request, entity_type="export", entity_id=e.id,
                 project_id=e.project_id, details={"sha256": e.sha256})
    db.commit()
    name = f"scriptorium-{e.scope}-{_ascii(e.options.get('title') or str(e.scope_id))}-{str(e.id)[:8]}.zip"
    return StreamingResponse(get_storage().iter_bytes(e.storage_key), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-SHA256": e.sha256,
                                      "Content-Length": str(e.size_bytes)})


class TrainingDataExportIn(BaseModel):
    normalization: Literal["NFC", "NFD", "NFKC", "NFKD"] | None = None


@router.post("/evaluation-datasets/{dataset_id}/training-data-export", status_code=201)
def export_training_data(dataset_id: uuid.UUID, body: TrainingDataExportIn, request: Request, user: CurrentUser, db: DB):
    """Kraken / eScriptorium-ready PAGE XML (line polygons, baselines, reference text) of every split."""
    from app.models import EvaluationDataset

    ds = db.get(EvaluationDataset, dataset_id)
    if ds is None:
        raise not_found("evaluation dataset")
    project, _ = load_project(db, user, ds.project_id, P.EXPORT)
    e = Export(id=uuid.uuid4(), project_id=project.id, scope="training-data", scope_id=ds.id, formats=["page"],
               status="queued", created_by_id=user.id,
               options={"layer": "ground-truth", "normalization": body.normalization, "title": ds.name})
    db.add(e)
    audit.record(db, "export.requested", actor_id=user.id, request=request, entity_type="export", entity_id=e.id,
                 project_id=project.id, institution_id=project.institution_id, details={"scope": "training-data", "dataset": ds.name})
    db.commit()
    from app.worker.tasks import build_export

    build_export.apply_async(args=[str(e.id)], task_id=f"export-{e.id}")
    db.refresh(e)
    return _export_out(e)
