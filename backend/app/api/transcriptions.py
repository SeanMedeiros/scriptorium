"""Transcription workspace API: transcriptions, revisions, line history, restore,
point-in-time state, renderings, annotations, project policy, manual segmentation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, load_project
from app.api.documents import asset_out
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.editorial import markup
from app.editorial.policy import TranscriptionPolicy
from app.editorial.service import Change, EditError, create_from_recognition, current_versions, restore, save_changes
from app.models import (
    Annotation,
    Collection,
    Document,
    ImageAsset,
    LineRecognition,
    LineStatus,
    LineVersion,
    ModelVersion,
    Page,
    Project,
    Recognition,
    Segmentation,
    TextLine,
    Transcription,
    TranscriptionRevision,
    User,
)
from app.services import audit

router = APIRouter(tags=["transcriptions"])


# ---------------------------------------------------------------- helpers
def _page_ctx(db, user, page_id: uuid.UUID, perm: P) -> tuple[Page, Project, frozenset]:
    page = db.get(Page, page_id)
    if page is None:
        raise not_found("Page")
    doc = db.get(Document, page.document_id)
    coll = db.get(Collection, doc.collection_id)
    project, perms = load_project(db, user, coll.project_id, perm)
    return page, project, perms


def _tx_ctx(db, user, transcription_id: uuid.UUID, perm: P):
    t = db.get(Transcription, transcription_id)
    if t is None:
        raise not_found("Transcription")
    page, project, perms = _page_ctx(db, user, t.page_id, perm)
    return t, page, project, perms


def _err(e: EditError) -> ApiError:
    return ApiError(e.status, e.code, e.message, details=e.details)


def _authors(db, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _line_state(v: LineVersion, authors: dict) -> dict:
    return {
        "version_id": str(v.id), "text": v.text, "normalized_text": v.normalized_text, "note": v.note,
        "status": v.status.value, "has_uncertain": v.has_uncertain, "has_illegible": v.has_illegible,
        "source": v.source, "author": authors.get(v.author_id), "created_at": v.created_at.isoformat(),
    }


def _renderings(text: str) -> dict:
    try:
        return {m: markup.render(text, m) for m in ("diplomatic", "expanded", "reading")}
    except markup.MarkupError:
        return {}


def _tx_out(db, t: Transcription, perms: frozenset, at_revision: int | None = None) -> dict:
    seg = db.get(Segmentation, t.segmentation_id)
    lines = db.scalars(select(TextLine).where(TextLine.segmentation_id == seg.id).order_by(TextLine.order_index)).all()
    versions = current_versions(db, t, at_revision)
    htr = {}
    model = None
    if t.base_recognition_id:
        rec = db.get(Recognition, t.base_recognition_id)
        mv = db.get(ModelVersion, rec.model_version_id)
        model = {"name": mv.model.name, "version": mv.version, "doi": mv.doi, "unicode_normalization": mv.unicode_normalization,
                 "recognition_id": str(rec.id), "model_version_id": str(mv.id)}
        htr = {lr.line_id: (lr.text, lr.mean_confidence, None) for lr in
               db.scalars(select(LineRecognition).where(LineRecognition.recognition_id == rec.id))}
    if t.origin == "combined":
        # Each line's original is the recognised line it was taken from (kept on the initial version).
        rows = db.execute(select(LineVersion.line_id, LineRecognition, ModelVersion)
                          .join(TranscriptionRevision, TranscriptionRevision.id == LineVersion.revision_id)
                          .join(LineRecognition, LineRecognition.id == LineVersion.origin_line_recognition_id)
                          .join(Recognition, Recognition.id == LineRecognition.recognition_id)
                          .join(ModelVersion, ModelVersion.id == Recognition.model_version_id)
                          .where(LineVersion.transcription_id == t.id, TranscriptionRevision.number == 1)).all()
        htr = {lid: (lr.text, lr.mean_confidence, f"{mv.model.name} · {mv.version}") for lid, lr, mv in rows}
    authors = _authors(db, [v.author_id for v in versions.values()])
    out_lines = []
    counts = {s.value: 0 for s in LineStatus} | {"uncertain": 0, "illegible": 0}
    for ln in lines:
        v = versions.get(ln.id)
        st = _line_state(v, authors) if v else None
        if v:
            counts[v.status.value] += 1
            counts["uncertain"] += int(v.has_uncertain)
            counts["illegible"] += int(v.has_illegible)
        h = htr.get(ln.id)
        out_lines.append({
            "line_id": str(ln.id), "order_index": ln.order_index, "bbox": ln.bbox, "boundary": ln.boundary,
            "baseline": ln.baseline, "region_id": str(ln.region_id) if ln.region_id else None,
            "original": {"text": h[0], "model_confidence": h[1], "model": h[2]} if h else None,
            "current": st, "renderings": _renderings(v.text) if v else {},
        })
    return {
        "id": str(t.id), "page_id": str(t.page_id), "segmentation_id": str(t.segmentation_id),
        "base_recognition_id": str(t.base_recognition_id) if t.base_recognition_id else None, "model": model,
        "origin": t.origin, "comparison_id": str(t.comparison_id) if t.comparison_id else None,
        "policy": t.policy, "markup_version": t.markup_version, "head_revision": t.head_revision,
        "viewing_revision": at_revision if at_revision is not None else t.head_revision,
        "input_asset": asset_out(db.get(ImageAsset, seg.input_asset_id)).model_dump(mode="json"),
        "permissions": sorted(p.value for p in perms), "counts": counts, "lines": out_lines,
        "created_at": t.created_at.isoformat(),
    }


# ---------------------------------------------------------------- schemas
class CreateIn(BaseModel):
    recognition_id: uuid.UUID


class ChangeIn(BaseModel):
    line_id: uuid.UUID
    expected_version_id: uuid.UUID | None
    text: str | None = Field(default=None, max_length=10000)
    normalized_text: str | None = Field(default=None, max_length=10000)
    note: str | None = Field(default=None, max_length=10000)
    status: LineStatus | None = None
    clear_normalized: bool = False
    clear_note: bool = False

    @field_validator("text", "normalized_text")
    @classmethod
    def _no_newlines(cls, v):
        if v is not None and ("\n" in v or "\r" in v):
            raise ValueError("a line cannot contain line breaks")
        return v


class SaveIn(BaseModel):
    changes: list[ChangeIn] = Field(min_length=1, max_length=2000)
    message: str | None = Field(default=None, max_length=500)


class RestoreIn(BaseModel):
    version_id: uuid.UUID


class AnnotationIn(BaseModel):
    line_id: uuid.UUID | None = None
    kind: Literal["comment", "query", "palaeography"] = "comment"
    body: str = Field(min_length=1, max_length=10000)


class PolicyIn(TranscriptionPolicy):
    pass


# ---------------------------------------------------------------- transcriptions
@router.get("/pages/{page_id}/transcriptions")
def list_transcriptions(page_id: uuid.UUID, user: CurrentUser, db: DB):
    page, _, _ = _page_ctx(db, user, page_id, P.VIEW)
    out = []
    for t in db.scalars(select(Transcription).where(Transcription.page_id == page.id).order_by(Transcription.created_at.desc())):
        rec = db.get(Recognition, t.base_recognition_id) if t.base_recognition_id else None
        mv = db.get(ModelVersion, rec.model_version_id) if rec else None
        out.append({"id": str(t.id), "base_recognition_id": str(t.base_recognition_id) if t.base_recognition_id else None,
                    "model": f"{mv.model.name} · {mv.version}" if mv else None, "head_revision": t.head_revision,
                    "origin": t.origin,
                    "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()})
    return out


@router.post("/pages/{page_id}/transcriptions", status_code=201)
def create_transcription(page_id: uuid.UUID, body: CreateIn, request: Request, user: CurrentUser, db: DB):
    page, project, perms = _page_ctx(db, user, page_id, P.TRANSCRIBE)
    rec = db.get(Recognition, body.recognition_id)
    if rec is None or rec.page_id != page.id:
        raise ApiError(422, "invalid_recognition", "The recognition does not belong to this page")
    existed = db.scalar(select(Transcription.id).where(Transcription.page_id == page.id,
                                                       Transcription.base_recognition_id == rec.id,
                                                       Transcription.origin == "recognition"))
    t = create_from_recognition(db, rec, author_id=user.id, project_policy=project.transcription_policy or None)
    if not existed:
        audit.record(db, "transcription.create", actor_id=user.id, request=request, entity_type="transcription",
                     entity_id=t.id, project_id=project.id, details={"recognition_id": str(rec.id)})
    db.commit()
    return _tx_out(db, t, perms)


@router.get("/transcriptions/{transcription_id}")
def get_transcription(transcription_id: uuid.UUID, user: CurrentUser, db: DB, at_revision: int | None = None):
    t, _, _, perms = _tx_ctx(db, user, transcription_id, P.VIEW)
    if at_revision is not None and not (1 <= at_revision <= t.head_revision):
        raise ApiError(422, "invalid_revision", f"Revision must be between 1 and {t.head_revision}")
    return _tx_out(db, t, perms, at_revision)


@router.post("/transcriptions/{transcription_id}/revisions", status_code=201)
def save(transcription_id: uuid.UUID, body: SaveIn, request: Request, user: CurrentUser, db: DB):
    t, _, project, perms = _tx_ctx(db, user, transcription_id, P.VIEW)
    edits_text = any(c.text is not None or c.normalized_text is not None or c.note is not None for c in body.changes)
    if edits_text and P.TRANSCRIBE not in perms:
        raise ApiError(403, "forbidden", "Editing requires the transcription permission")
    if not edits_text and P.REVIEW not in perms and P.TRANSCRIBE not in perms:
        raise ApiError(403, "forbidden", "Changing line status requires the transcription or review permission")
    try:
        rev = save_changes(db, t, [Change(**c.model_dump()) for c in body.changes], author_id=user.id,
                           message=body.message, can_review=P.REVIEW in perms)
    except EditError as e:
        db.rollback()
        raise _err(e)
    if rev is None:
        db.rollback()
        return {"revision": None, "transcription": _tx_out(db, t, perms)}
    audit.record(db, f"transcription.{rev.kind}", actor_id=user.id, request=request, entity_type="transcription",
                 entity_id=t.id, project_id=project.id, details={"revision": rev.number, "lines": rev.change_count})
    db.commit()
    return {"revision": {"number": rev.number, "kind": rev.kind, "change_count": rev.change_count},
            "transcription": _tx_out(db, t, perms)}


@router.get("/transcriptions/{transcription_id}/revisions")
def revisions(transcription_id: uuid.UUID, user: CurrentUser, db: DB):
    t, _, _, _ = _tx_ctx(db, user, transcription_id, P.VIEW)
    revs = db.scalars(select(TranscriptionRevision).where(TranscriptionRevision.transcription_id == t.id)
                      .order_by(TranscriptionRevision.number.desc())).all()
    authors = _authors(db, [r.author_id for r in revs])
    return [{"number": r.number, "kind": r.kind, "message": r.message, "change_count": r.change_count,
             "author": authors.get(r.author_id), "created_at": r.created_at.isoformat()} for r in revs]


@router.get("/transcriptions/{transcription_id}/lines/{line_id}/history")
def line_history(transcription_id: uuid.UUID, line_id: uuid.UUID, user: CurrentUser, db: DB):
    t, _, _, _ = _tx_ctx(db, user, transcription_id, P.VIEW)
    rows = db.execute(select(LineVersion, TranscriptionRevision.number).join(TranscriptionRevision)
                      .where(LineVersion.transcription_id == t.id, LineVersion.line_id == line_id)
                      .order_by(TranscriptionRevision.number.desc())).all()
    if not rows:
        raise not_found("Line")
    authors = _authors(db, [v.author_id for v, _ in rows])
    return [{"revision": n, **_line_state(v, authors),
             "restored_from_id": str(v.restored_from_id) if v.restored_from_id else None} for v, n in rows]


@router.post("/transcriptions/{transcription_id}/lines/{line_id}/restore", status_code=201)
def restore_line(transcription_id: uuid.UUID, line_id: uuid.UUID, body: RestoreIn, request: Request, user: CurrentUser, db: DB):
    t, _, project, perms = _tx_ctx(db, user, transcription_id, P.TRANSCRIBE)
    try:
        rev = restore(db, t, line_id, body.version_id, author_id=user.id)
    except EditError as e:
        db.rollback()
        raise _err(e)
    audit.record(db, "transcription.restore", actor_id=user.id, request=request, entity_type="transcription", entity_id=t.id,
                 project_id=project.id, details={"revision": rev.number, "line_id": str(line_id), "version_id": str(body.version_id)})
    db.commit()
    return {"revision": {"number": rev.number, "kind": rev.kind}, "transcription": _tx_out(db, t, perms)}


@router.put("/transcriptions/{transcription_id}/policy")
def set_policy(transcription_id: uuid.UUID, body: PolicyIn, request: Request, user: CurrentUser, db: DB):
    t, _, project, perms = _tx_ctx(db, user, transcription_id, P.EDIT_METADATA)
    t.policy = TranscriptionPolicy.model_validate(body.model_dump()).model_dump()
    audit.record(db, "transcription.policy", actor_id=user.id, request=request, entity_type="transcription", entity_id=t.id,
                 project_id=project.id, details=t.policy)
    db.commit()
    return _tx_out(db, t, perms)


@router.put("/projects/{project_id}/transcription-policy")
def set_project_policy(project_id: uuid.UUID, body: PolicyIn, request: Request, user: CurrentUser, db: DB):
    project, _ = load_project(db, user, project_id, P.EDIT_METADATA)
    project.transcription_policy = TranscriptionPolicy.model_validate(body.model_dump()).model_dump()
    audit.record(db, "project.transcription_policy", actor_id=user.id, request=request, entity_type="project",
                 entity_id=project.id, project_id=project.id, details=project.transcription_policy)
    db.commit()
    return project.transcription_policy


@router.post("/editorial/validate")
def validate_markup(body: dict, _: CurrentUser):
    """Validate and render a line of editorial markup (used by the editor while typing)."""
    text = str(body.get("text", ""))[:10000]
    err = markup.validate(text)
    if err:
        return {"valid": False, "error": err.message, "position": err.position}
    st = markup.stats(text)
    return {"valid": True, "renderings": _renderings(text), "stats": st.__dict__}


# ---------------------------------------------------------------- annotations
def _ann_out(a: Annotation, authors: dict) -> dict:
    return {"id": str(a.id), "line_id": str(a.line_id) if a.line_id else None, "kind": a.kind, "body": a.body,
            "author": authors.get(a.author_id), "created_at": a.created_at.isoformat(),
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None}


@router.get("/transcriptions/{transcription_id}/annotations")
def list_annotations(transcription_id: uuid.UUID, user: CurrentUser, db: DB):
    t, _, _, _ = _tx_ctx(db, user, transcription_id, P.VIEW)
    rows = db.scalars(select(Annotation).where(Annotation.transcription_id == t.id).order_by(Annotation.created_at)).all()
    authors = _authors(db, [a.author_id for a in rows])
    return [_ann_out(a, authors) for a in rows]


@router.post("/transcriptions/{transcription_id}/annotations", status_code=201)
def add_annotation(transcription_id: uuid.UUID, body: AnnotationIn, request: Request, user: CurrentUser, db: DB):
    t, _, project, perms = _tx_ctx(db, user, transcription_id, P.VIEW)
    if P.TRANSCRIBE not in perms and P.REVIEW not in perms:
        raise ApiError(403, "forbidden", "Annotating requires the transcription or review permission")
    if body.line_id and db.scalar(select(TextLine.segmentation_id).where(TextLine.id == body.line_id)) != t.segmentation_id:
        raise ApiError(422, "invalid_line", "The line does not belong to this transcription")
    a = Annotation(transcription_id=t.id, line_id=body.line_id, kind=body.kind, body=body.body, author_id=user.id)
    db.add(a)
    db.flush()
    audit.record(db, "annotation.create", actor_id=user.id, request=request, entity_type="annotation", entity_id=a.id,
                 project_id=project.id)
    db.commit()
    return _ann_out(a, _authors(db, [user.id]))


@router.post("/annotations/{annotation_id}/resolve")
def resolve_annotation(annotation_id: uuid.UUID, user: CurrentUser, db: DB):
    a = db.get(Annotation, annotation_id)
    if a is None:
        raise not_found("Annotation")
    t, _, _, perms = _tx_ctx(db, user, a.transcription_id, P.VIEW)
    if P.REVIEW not in perms and a.author_id != user.id:
        raise ApiError(403, "forbidden", "Only the author or a reviewer can resolve an annotation")
    a.resolved_at, a.resolved_by_id = datetime.now(timezone.utc), user.id
    db.commit()
    return _ann_out(a, _authors(db, [a.author_id]))


# ---------------------------------------------------------------- manual segmentation
class LineGeomIn(BaseModel):
    baseline: list[list[int]] = Field(min_length=2, max_length=2000)
    boundary: list[list[int]] | None = Field(default=None, max_length=5000)
    source_line_id: uuid.UUID | None = None  # line this one was derived from (for provenance)

    @field_validator("baseline", "boundary")
    @classmethod
    def _pts(cls, v):
        if v is not None and any(len(p) != 2 or p[0] < 0 or p[1] < 0 for p in v):
            raise ValueError("points must be [x, y] pairs of non-negative integers")
        return v


class SegmentationIn(BaseModel):
    parent_segmentation_id: uuid.UUID
    lines: list[LineGeomIn] = Field(min_length=1, max_length=2000)
    message: str | None = Field(default=None, max_length=500)


@router.post("/pages/{page_id}/segmentations", status_code=201)
def create_manual_segmentation(page_id: uuid.UUID, body: SegmentationIn, request: Request, user: CurrentUser, db: DB):
    """Save an adjusted segmentation as a NEW segmentation (the parent is never modified).
    Lines without a boundary polygon get one computed by Kraken when recognition runs."""
    page, project, _ = _page_ctx(db, user, page_id, P.TRANSCRIBE)
    parent = db.get(Segmentation, body.parent_segmentation_id)
    if parent is None or parent.page_id != page.id:
        raise ApiError(422, "invalid_parent", "The parent segmentation does not belong to this page")
    asset = db.get(ImageAsset, parent.input_asset_id)
    for ln in body.lines:
        for x, y in ln.baseline + (ln.boundary or []):
            if x > (asset.width or 10**9) or y > (asset.height or 10**9):
                raise ApiError(422, "out_of_bounds", "A point lies outside the page image", details={"point": [x, y]})
    seg = Segmentation(id=uuid.uuid4(), page_id=page.id, source="manual", parent_segmentation_id=parent.id,
                       input_asset_id=parent.input_asset_id, text_direction=parent.text_direction,
                       config={"edited_from": str(parent.id), "message": body.message}, software={},
                       created_by_id=user.id)
    db.add(seg)
    db.flush()
    for i, ln in enumerate(body.lines):
        pts = ln.boundary or ln.baseline
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        db.add(TextLine(segmentation_id=seg.id, order_index=i, baseline=ln.baseline, boundary=ln.boundary,
                        bbox=[min(xs), min(ys), max(xs), max(ys)],
                        tags={"derived_from": str(ln.source_line_id)} if ln.source_line_id else {}))
    audit.record(db, "segmentation.manual", actor_id=user.id, request=request, entity_type="segmentation", entity_id=seg.id,
                 project_id=project.id, details={"parent": str(parent.id), "lines": len(body.lines)})
    db.commit()
    return {"id": str(seg.id), "lines": len(body.lines),
            "lines_without_boundary": sum(1 for ln in body.lines if ln.boundary is None)}
