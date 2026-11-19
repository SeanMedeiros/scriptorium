"""Documents, uploads, pages and asset delivery."""
from __future__ import annotations

import json
import tempfile
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentUser, load_project
from app.config import get_settings
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.models import Collection, Document, ImageAsset, Institution, Page, Recognition, RecognitionJob
from app.schemas_recognition import AssetOut, DocumentDetailOut, DocumentMetaIn, DocumentOut, PageOut
from app.services import asset_urls, audit, malware
from app.services import retention
from app.services.ingest import IngestError, sniff, store_original
from app.storage import ObjectNotFound, get_storage

router = APIRouter(tags=["documents"])


def asset_out(a: ImageAsset | None) -> AssetOut | None:
    if a is None:
        return None
    o = AssetOut.model_validate(a)
    if a.mime_type.startswith("image/") and a.mime_type != "image/tiff":
        o.url = asset_urls.signed_url(a.id)
    return o


def _collection(db, user, project_id, collection_id, perm):
    project, perms = load_project(db, user, project_id, perm)
    c = db.get(Collection, collection_id)
    if c is None or c.project_id != project.id:
        raise not_found("Collection")
    return project, c


def _document(db, user, document_id, perm):
    d = db.get(Document, document_id)
    if d is None:
        raise not_found("Document")
    c = db.get(Collection, d.collection_id)
    project, _ = load_project(db, user, c.project_id, perm)
    return project, c, d


def _doc_out(db, d: Document, *, page_count: int | None = None, asset: ImageAsset | None = None) -> DocumentOut:
    o = DocumentOut.model_validate(d)
    o.page_count = page_count if page_count is not None else (
        db.scalar(select(func.count(Page.id)).where(Page.document_id == d.id)) or 0)
    if d.source_asset_id:
        o.source = asset_out(asset if asset is not None else db.get(ImageAsset, d.source_asset_id))
    c = db.get(Collection, d.collection_id)
    o.collection_title = c.title
    o.project_id = c.project_id
    o.project_title = c.project.title
    return o


def page_out(db, p: Page) -> PageOut:
    o = PageOut.model_validate(p)
    o.image = asset_out(db.get(ImageAsset, p.image_asset_id))
    o.latest_job_status = db.scalar(select(RecognitionJob.status).where(RecognitionJob.page_id == p.id)
                                    .order_by(RecognitionJob.submitted_at.desc()).limit(1))
    o.recognition_count = db.scalar(select(func.count(Recognition.id)).where(Recognition.page_id == p.id)) or 0
    return o


def _enqueue_ingest(document_id: uuid.UUID) -> None:
    from app.worker.tasks import ingest_document

    ingest_document.delay(str(document_id))


@router.post("/projects/{project_id}/collections/{collection_id}/documents", response_model=list[DocumentOut],
             status_code=201)
def upload_documents(project_id: uuid.UUID, collection_id: uuid.UUID, request: Request, user: CurrentUser, db: DB,
                     files: Annotated[list[UploadFile], File(description="PNG, JPEG, TIFF or PDF; one document per file")],
                     metadata: Annotated[str | None, Form(description="JSON object with document metadata")] = None):
    """Upload one or more files. Each file becomes a document; pages are extracted in the background."""
    s = get_settings()
    project, collection = _collection(db, user, project_id, collection_id, P.UPLOAD)
    if not files:
        raise ApiError(422, "no_files", "No file was uploaded")
    if len(files) > s.max_files_per_upload:
        raise ApiError(413, "too_many_files", f"At most {s.max_files_per_upload} files per upload")
    try:
        meta = DocumentMetaIn.model_validate(json.loads(metadata) if metadata else {})
    except (json.JSONDecodeError, ValidationError) as e:
        raise ApiError(422, "invalid_metadata", "metadata must be a valid JSON object", details=str(e)[:2000])

    storage = get_storage()
    # Institutional storage policy (Phase 7): per-file limit, accepted types and storage quota.
    inst = db.get(Institution, project.institution_id) if project.institution_id else None
    limits = retention.effective_limits(inst)
    max_bytes = limits["max_upload_bytes"]
    used = retention.originals_usage(db, inst.id) if inst and limits["max_storage_bytes"] else 0
    created: list[Document] = []
    for f in files:
        name = (f.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:500]
        # Spool to disk with a hard size limit (never trust Content-Length).
        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as tmp:
            total = 0
            while chunk := f.file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ApiError(413, "file_too_large", f"'{name}' exceeds {max_bytes // (1024 * 1024)} MB",
                                   details={"file": name, "limit_bytes": max_bytes})
                tmp.write(chunk)
            if total == 0:
                raise ApiError(422, "empty_file", f"'{name}' is empty", details={"file": name})
            tmp.seek(0)
            sniffed = sniff(tmp.read(16))
            tmp.seek(0)
            if sniffed and sniffed not in limits["allowed_mime_types"]:
                raise ApiError(415, "type_not_allowed_by_policy",
                               f"{name}: {sniffed} uploads are not accepted by the institution's storage policy",
                               details={"file": name, "allowed": limits["allowed_mime_types"]})
            if limits["max_storage_bytes"] is not None:
                if used + total > limits["max_storage_bytes"]:
                    raise ApiError(413, "storage_quota_exceeded",
                                   f"{name}: the institution's storage quota would be exceeded",
                                   details={"file": name, "quota_bytes": limits["max_storage_bytes"], "used_bytes": used})
                used += total
            # Institutional malware scanning (clamd INSTREAM) before anything is stored; fail-closed.
            malware.scan_or_raise(tmp, name, kind="document", actor_id=user.id, request=request,
                                  project_id=project.id, institution_id=project.institution_id)
            try:
                original = store_original(storage, tmp, name, user.id)
            except IngestError as e:
                raise ApiError(415 if e.code in ("unsupported_type", "unsupported_extension", "type_mismatch") else 422,
                               e.code, f"{name}: {e.message}", details={"file": name, **e.details})
        db.add(original)
        db.flush()
        title = meta.title if (meta.title and len(files) == 1) else name.rsplit(".", 1)[0]
        doc = Document(collection_id=collection.id, created_by_id=user.id, source_asset_id=original.id,
                       ingest_status="pending", **{**meta.model_dump(exclude={"title"}), "title": title})
        db.add(doc)
        db.flush()
        audit.record(db, "document.upload", actor_id=user.id, request=request, entity_type="document", entity_id=doc.id,
                     project_id=project.id, institution_id=project.institution_id,
                     details={"file": name, "sha256": original.sha256, "size": original.size_bytes, "mime": original.mime_type})
        created.append(doc)
    db.commit()
    for doc in created:
        _enqueue_ingest(doc.id)
    return [_doc_out(db, d) for d in created]


@router.get("/projects/{project_id}/collections/{collection_id}/documents", response_model=dict)
def list_documents(project_id: uuid.UUID, collection_id: uuid.UUID, user: CurrentUser, db: DB, q: str | None = None,
                   language: str | None = None, limit: int = Query(100, le=500), offset: int = 0):
    _, collection = _collection(db, user, project_id, collection_id, P.VIEW)
    stmt = select(Document).where(Document.collection_id == collection.id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Document.title).like(like),
                              func.lower(func.coalesce(Document.archival_reference, "")).like(like),
                              func.lower(func.coalesce(Document.description, "")).like(like)))
    if language:
        stmt = stmt.where(Document.languages.contains([language]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Document.created_at, Document.id).limit(limit).offset(offset)).all()
    # Batched lookups (Phase 7 performance review): one query for page counts and one for source
    # assets instead of two queries per listed document.
    ids = [d.id for d in rows]
    counts = dict(db.execute(select(Page.document_id, func.count(Page.id)).where(Page.document_id.in_(ids))
                             .group_by(Page.document_id)).all()) if ids else {}
    asset_ids = [d.source_asset_id for d in rows if d.source_asset_id]
    assets = {a.id: a for a in db.scalars(select(ImageAsset).where(ImageAsset.id.in_(asset_ids)))} if asset_ids else {}
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_doc_out(db, d, page_count=counts.get(d.id, 0), asset=assets.get(d.source_asset_id)).model_dump(mode="json")
                      for d in rows]}


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: uuid.UUID, user: CurrentUser, db: DB):
    _, _, d = _document(db, user, document_id, P.VIEW)
    out = DocumentDetailOut(**_doc_out(db, d).model_dump())
    out.pages = [page_out(db, p) for p in db.scalars(select(Page).where(Page.document_id == d.id).order_by(Page.sequence))]
    return out


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def update_document(document_id: uuid.UUID, body: DocumentMetaIn, request: Request, user: CurrentUser, db: DB):
    project, _, d = _document(db, user, document_id, P.UPLOAD)
    changes = body.model_dump(exclude_unset=True)
    if "title" in changes and not changes["title"]:
        changes.pop("title")
    for k, v in changes.items():
        setattr(d, k, v)
    audit.record(db, "document.update", actor_id=user.id, request=request, entity_type="document", entity_id=d.id,
                 project_id=project.id, details={"fields": sorted(changes)})
    db.commit()
    return _doc_out(db, d)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    project, _, d = _document(db, user, document_id, P.DELETE)
    audit.record(db, "document.delete", actor_id=user.id, request=request, entity_type="document", entity_id=d.id,
                 project_id=project.id, details={"title": d.title})
    db.delete(d)
    db.commit()
    return Response(status_code=204)


@router.get("/pages/{page_id}", response_model=PageOut)
def get_page(page_id: uuid.UUID, user: CurrentUser, db: DB):
    p = db.get(Page, page_id)
    if p is None:
        raise not_found("Page")
    _document(db, user, p.document_id, P.VIEW)
    return page_out(db, p)


@router.get("/documents/{document_id}/original")
def download_original(document_id: uuid.UUID, user: CurrentUser, db: DB):
    _, _, d = _document(db, user, document_id, P.VIEW)
    a = db.get(ImageAsset, d.source_asset_id)
    storage = get_storage()
    name = (a.original_filename or f"{d.id}").replace('"', "")
    return StreamingResponse(storage.iter_bytes(a.storage_key), media_type=a.mime_type,
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "X-Content-SHA256": a.sha256})


@router.get("/assets/{asset_id}/content", include_in_schema=True)
def asset_content(asset_id: uuid.UUID, exp: int, sig: str, db: DB):
    """Signed, time-limited asset delivery (URLs are issued only to authorised users)."""
    if not asset_urls.verify(asset_id, exp, sig):
        raise ApiError(403, "invalid_signature", "The asset link is invalid or has expired")
    a = db.get(ImageAsset, asset_id)
    if a is None:
        raise not_found("Asset")
    try:
        it = get_storage().iter_bytes(a.storage_key)
    except ObjectNotFound:
        raise ApiError(410, "asset_missing", "The stored object is missing")
    return StreamingResponse(it, media_type=a.mime_type,
                             headers={"Cache-Control": "private, max-age=3600", "X-Content-SHA256": a.sha256})


@router.post("/documents/{document_id}/reingest", status_code=202)
def reingest(document_id: uuid.UUID, user: CurrentUser, db: DB):
    _, _, d = _document(db, user, document_id, P.UPLOAD)
    if d.ingest_status != "failed":
        raise ApiError(409, "not_failed", "Only failed ingestions can be retried")
    d.ingest_status, d.ingest_error = "pending", None
    db.commit()
    _enqueue_ingest(d.id)
    return {"status": "pending"}
