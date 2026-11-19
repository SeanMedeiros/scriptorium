"""Human transcription: working transcriptions, immutable revisions, line versions, annotations.

Layers per line (master prompt §9):
1. original HTR output        → line_recognitions.text (immutable, never copied over)
2. researcher-corrected text  → line_versions.text (editorial markup v1)
3. editorially normalised     → line_versions.normalized_text (entered by the researcher)
4. expanded abbreviations     → derived from the `{abbr|expansion}` markup the researcher wrote
5. notes                      → line_versions.note and the annotations table

Every save creates one TranscriptionRevision and one LineVersion per changed line.
Both tables reject UPDATE at database level; the current state of a line is its latest version.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Timestamps, UUIDPk, utcnow
from app.models.core import _enum


class LineStatus(str, enum.Enum):
    UNREVIEWED = "unreviewed"  # as produced by the model, not checked by a person
    CORRECTED = "corrected"  # edited / checked by a transcriber
    REVIEWED = "reviewed"  # approved by a reviewer


class Transcription(UUIDPk, Timestamps, Base):
    __tablename__ = "transcriptions"

    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="RESTRICT"), index=True)
    base_recognition_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recognitions.id", ondelete="RESTRICT"), index=True)
    # Policy copied from the project at creation and editable afterwards; stored with every revision.
    policy: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    markup_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    head_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # recognition: initialised from one recognition · combined: lines chosen from several (model comparison)
    origin: Mapped[str] = mapped_column(String(20), default="recognition", server_default="recognition")
    comparison_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comparisons.id", ondelete="SET NULL"), index=True)

    __table_args__ = (UniqueConstraint("page_id", "base_recognition_id", name="uq_transcription_base"),)


class TranscriptionRevision(UUIDPk, Base):
    __tablename__ = "transcription_revisions"

    transcription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcriptions.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    kind: Mapped[str] = mapped_column(String(20))  # initial | edit | restore | review
    message: Mapped[str | None] = mapped_column(String(500))
    change_count: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (UniqueConstraint("transcription_id", "number", name="uq_revision_number"),)


class LineVersion(UUIDPk, Base):
    __tablename__ = "line_versions"

    transcription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcriptions.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcription_revisions.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("text_lines.id", ondelete="CASCADE"), index=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("line_versions.id", ondelete="SET NULL"), index=True)
    restored_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("line_versions.id", ondelete="SET NULL"), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LineStatus] = mapped_column(_enum(LineStatus, "line_status"))
    has_uncertain: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_illegible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(20))  # htr | human | restore
    # Combined transcriptions (model comparison): the recognised line this text was taken from.
    origin_line_recognition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("line_recognitions.id", ondelete="RESTRICT"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (Index("ix_line_versions_current", "transcription_id", "line_id", "created_at"),)


class Annotation(UUIDPk, Timestamps, Base):
    """Editorial comments and queries attached to a transcription or a line."""

    __tablename__ = "annotations"

    transcription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcriptions.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("text_lines.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # comment | query | palaeography
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
