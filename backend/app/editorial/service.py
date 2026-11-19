"""Transcription editing: creation from a recognition, saving revisions with optimistic
concurrency, history, restoration and point-in-time state."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.editorial import markup
from app.editorial.policy import TranscriptionPolicy, check
from app.models import (
    LineRecognition,
    LineStatus,
    LineVersion,
    ModelVersion,
    Recognition,
    TextLine,
    Transcription,
    TranscriptionRevision,
)


class EditError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details or {}, status


@dataclass
class Change:
    line_id: uuid.UUID
    expected_version_id: uuid.UUID | None
    text: str | None = None
    normalized_text: str | None = None
    note: str | None = None
    status: LineStatus | None = None
    clear_normalized: bool = False
    clear_note: bool = False


def policy_of(t: Transcription) -> TranscriptionPolicy:
    return TranscriptionPolicy.model_validate(t.policy or {})


def current_versions(db: Session, t: Transcription, at_revision: int | None = None) -> dict[uuid.UUID, LineVersion]:
    """Latest version of every line (optionally as of a past revision number)."""
    stmt = (select(LineVersion).join(TranscriptionRevision, TranscriptionRevision.id == LineVersion.revision_id)
            .where(LineVersion.transcription_id == t.id)
            .order_by(LineVersion.line_id, TranscriptionRevision.number.desc())
            .ext(distinct_on(LineVersion.line_id)))
    if at_revision is not None:
        stmt = stmt.where(TranscriptionRevision.number <= at_revision)
    return {v.line_id: v for v in db.scalars(stmt)}


def _flags(text: str) -> tuple[bool, bool]:
    st = markup.stats(text)
    return st.uncertain > 0, st.illegible > 0


def _new_revision(db: Session, t: Transcription, *, author_id, kind: str, message: str | None, count: int) -> TranscriptionRevision:
    t.head_revision = (t.head_revision or 0) + 1
    rev = TranscriptionRevision(id=uuid.uuid4(), transcription_id=t.id, number=t.head_revision, author_id=author_id,
                                kind=kind, message=message, change_count=count, policy=t.policy)
    db.add(rev)
    db.flush()  # the revision row must exist before its line versions (no ORM relationship orders them)
    return rev


def create_from_recognition(db: Session, recognition: Recognition, *, author_id, project_policy: dict | None) -> Transcription:
    existing = db.scalar(select(Transcription).where(Transcription.page_id == recognition.page_id,
                                                     Transcription.base_recognition_id == recognition.id,
                                                     Transcription.origin == "recognition"))
    if existing:
        return existing
    mv = db.get(ModelVersion, recognition.model_version_id)
    curated = (mv.model.curated or {}) if mv else {}
    if project_policy:
        policy = TranscriptionPolicy.model_validate(project_policy)
    else:
        policy = TranscriptionPolicy.for_model(mv.unicode_normalization if mv else None, curated.get("transcription_convention"))
    t = Transcription(id=uuid.uuid4(), page_id=recognition.page_id, segmentation_id=recognition.segmentation_id,
                      base_recognition_id=recognition.id, policy=policy.model_dump(), created_by_id=author_id, head_revision=0)
    db.add(t)
    db.flush()
    lines = db.scalars(select(TextLine).where(TextLine.segmentation_id == recognition.segmentation_id)
                       .order_by(TextLine.order_index)).all()
    htr = {lr.line_id: lr for lr in db.scalars(select(LineRecognition).where(LineRecognition.recognition_id == recognition.id))}
    model_label = f"{mv.model.name} {mv.version}" if mv else "recognition"
    rev = _new_revision(db, t, author_id=author_id, kind="initial", message=f"initialised from {model_label} output",
                        count=len(lines))
    for ln in lines:
        # HTR text is copied verbatim; it may contain literal braces, which are escaped so that
        # the stored markup round-trips to exactly the model's output.
        lr = htr.get(ln.id)
        text = escape(lr.text if lr else "")
        db.add(LineVersion(transcription_id=t.id, revision_id=rev.id, line_id=ln.id, text=text, status=LineStatus.UNREVIEWED,
                           source="htr", author_id=None, has_uncertain=False, has_illegible=False,
                           origin_line_recognition_id=lr.id if lr else None))
    db.flush()
    return t


def create_combined(db: Session, primary: Recognition, chosen: dict[uuid.UUID, LineRecognition], *, author_id,
                    project_policy: dict | None, comparison_id: uuid.UUID | None, labels: dict[uuid.UUID, str]) -> Transcription:
    """A transcription whose initial text takes each line from the recognition the researcher chose.
    `chosen` maps each line of the primary segmentation to a LineRecognition; every line version keeps
    `origin_line_recognition_id`, so the origin of every segment stays traceable (§12)."""
    mv = db.get(ModelVersion, primary.model_version_id)
    curated = (mv.model.curated or {}) if mv else {}
    policy = (TranscriptionPolicy.model_validate(project_policy) if project_policy
              else TranscriptionPolicy.for_model(mv.unicode_normalization if mv else None, curated.get("transcription_convention")))
    t = Transcription(id=uuid.uuid4(), page_id=primary.page_id, segmentation_id=primary.segmentation_id,
                      base_recognition_id=primary.id, policy=policy.model_dump(), created_by_id=author_id, head_revision=0,
                      origin="combined", comparison_id=comparison_id)
    db.add(t)
    db.flush()
    lines = db.scalars(select(TextLine).where(TextLine.segmentation_id == primary.segmentation_id).order_by(TextLine.order_index)).all()
    used = sorted({labels[lr.recognition_id] for lr in chosen.values()})
    rev = _new_revision(db, t, author_id=author_id, kind="initial", message=f"combined from {', '.join(used)}", count=len(lines))
    for ln in lines:
        lr = chosen.get(ln.id)
        db.add(LineVersion(transcription_id=t.id, revision_id=rev.id, line_id=ln.id, text=escape(lr.text if lr else ""),
                           status=LineStatus.UNREVIEWED, source="htr", author_id=None, has_uncertain=False,
                           has_illegible=False, origin_line_recognition_id=lr.id if lr else None))
    db.flush()
    return t


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("|", "\\|")


def save_changes(db: Session, t: Transcription, changes: list[Change], *, author_id, message: str | None,
                 can_review: bool) -> TranscriptionRevision | None:
    """Apply edits atomically. Raises EditError(conflict) if any line changed since the client read it."""
    db.refresh(t, with_for_update=True)  # serialise saves on the same transcription
    policy = policy_of(t)
    current = current_versions(db, t)
    valid_lines = set(db.scalars(select(TextLine.id).where(TextLine.segmentation_id == t.segmentation_id)))
    conflicts, errors, prepared = [], [], []
    for ch in changes:
        if ch.line_id not in valid_lines:
            errors.append({"line_id": str(ch.line_id), "error": "line does not belong to this transcription"})
            continue
        cur = current.get(ch.line_id)
        if cur is not None and ch.expected_version_id != cur.id:
            conflicts.append({"line_id": str(ch.line_id), "current_version_id": str(cur.id), "current_text": cur.text,
                              "author_id": str(cur.author_id) if cur.author_id else None})
            continue
        text = cur.text if ch.text is None else markup.apply_unicode_policy(ch.text, policy.unicode_input)
        if ch.text is not None and (msg := check(text, policy)):
            errors.append({"line_id": str(ch.line_id), "error": msg})
            continue
        normalized = None if ch.clear_normalized else (
            cur.normalized_text if ch.normalized_text is None else markup.apply_unicode_policy(ch.normalized_text, policy.unicode_input))
        note = None if ch.clear_note else (cur.note if ch.note is None else ch.note)
        status = ch.status
        if status == LineStatus.REVIEWED and not can_review:
            errors.append({"line_id": str(ch.line_id), "error": "marking a line as reviewed requires the reviewer permission"})
            continue
        if status is None:
            status = LineStatus.CORRECTED if (ch.text is not None and text != cur.text) else cur.status
            if cur.status == LineStatus.REVIEWED and ch.text is not None and text != cur.text:
                status = LineStatus.CORRECTED  # an edit after review needs a new review
        if (text, normalized, note, status) == (cur.text, cur.normalized_text, cur.note, cur.status):
            continue  # nothing changed
        prepared.append((ch, cur, text, normalized, note, status))
    if conflicts:
        raise EditError("edit_conflict", "Some lines were changed by someone else since you loaded them",
                        {"conflicts": conflicts}, status=409)
    if errors:
        raise EditError("invalid_edit", "Some edits were rejected", {"errors": errors})
    if not prepared:
        return None
    only_review = all(ch.text is None and ch.normalized_text is None and ch.note is None for ch, *_ in prepared)
    rev = _new_revision(db, t, author_id=author_id, kind="review" if only_review else "edit", message=message,
                        count=len(prepared))
    for ch, cur, text, normalized, note, status in prepared:
        unc, ill = _flags(text)
        db.add(LineVersion(transcription_id=t.id, revision_id=rev.id, line_id=ch.line_id, previous_version_id=cur.id,
                           text=text, normalized_text=normalized, note=note, status=status, has_uncertain=unc,
                           has_illegible=ill, source="human", author_id=author_id))
    db.flush()
    return rev


def restore(db: Session, t: Transcription, line_id: uuid.UUID, version_id: uuid.UUID, *, author_id) -> TranscriptionRevision:
    db.refresh(t, with_for_update=True)
    old = db.get(LineVersion, version_id)
    if old is None or old.transcription_id != t.id or old.line_id != line_id:
        raise EditError("not_found", "Version not found for this line", status=404)
    cur = current_versions(db, t)[line_id]
    if cur.id == old.id:
        raise EditError("already_current", "This version is already the current one", status=409)
    rev = _new_revision(db, t, author_id=author_id, kind="restore", message=f"restored line version {str(old.id)[:8]}", count=1)
    db.add(LineVersion(transcription_id=t.id, revision_id=rev.id, line_id=line_id, previous_version_id=cur.id,
                       restored_from_id=old.id, text=old.text, normalized_text=old.normalized_text, note=old.note,
                       status=LineStatus.CORRECTED if old.status == LineStatus.REVIEWED else old.status,
                       has_uncertain=old.has_uncertain, has_illegible=old.has_illegible, source="restore", author_id=author_id))
    db.flush()
    return rev
