"""Research tools (Phase 5): ground truth, evaluation, model comparison and exports.

Ground truth (master prompt §11)
* An evaluation dataset belongs to a project and groups ground-truth pages, each assigned to
  exactly one split (train / validation / test).
* A ground-truth page is an immutable snapshot: a segmentation (lines with coordinates) and one
  reference text per line. It comes either from a fully *reviewed* human transcription (never from
  unreviewed model output) or from an imported ALTO / PAGE XML file.
* Evaluation runs recognise ground-truth pages *on the ground-truth segmentation*, so that the
  hypothesis is line-aligned with the reference, exactly like `ketos test`.
* Every result stores the normalisation used and the metric methodology version.

Ground-truth pages, lines and evaluation results reject UPDATE at database level.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamps, UUIDPk, utcnow

SPLITS = ("train", "validation", "test")


class EvaluationDataset(UUIDPk, Timestamps, Base):
    __tablename__ = "evaluation_datasets"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    pages: Mapped[list["GroundTruthPage"]] = relationship(back_populates="dataset", order_by="GroundTruthPage.created_at")

    __table_args__ = (UniqueConstraint("project_id", "name"),)


class GroundTruthPage(UUIDPk, Base):
    __tablename__ = "ground_truth_pages"

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="RESTRICT"), index=True)
    split: Mapped[str] = mapped_column(String(12))  # train | validation | test
    source: Mapped[str] = mapped_column(String(20))  # transcription | alto | page-xml
    # transcription: {transcription_id, revision, base_model}; import: {filename, sha256, software, …}
    source_details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    unicode_form: Mapped[str | None] = mapped_column(String(10))  # as detected in the reference text
    note: Mapped[str | None] = mapped_column(Text)  # e.g. "validation set used by the model authors"
    line_count: Mapped[int] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    # Withdrawal is recorded by a separate row (ground_truth_withdrawals); GT rows are never edited.

    dataset: Mapped[EvaluationDataset] = relationship(back_populates="pages")
    lines: Mapped[list["GroundTruthLine"]] = relationship(order_by="GroundTruthLine.order_index")

    __table_args__ = (UniqueConstraint("dataset_id", "page_id"),)


class GroundTruthWithdrawal(UUIDPk, Base):
    """Removing a page from a dataset keeps the snapshot (results stay interpretable)."""
    __tablename__ = "ground_truth_withdrawals"

    gt_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ground_truth_pages.id", ondelete="CASCADE"), unique=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    withdrawn_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    withdrawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class GroundTruthLine(UUIDPk, Base):
    __tablename__ = "ground_truth_lines"

    gt_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ground_truth_pages.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("text_lines.id", ondelete="RESTRICT"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # reference text, exact Unicode sequence as provided


class EvaluationRun(UUIDPk, Base):
    """One model version evaluated on one split of a dataset with a fixed methodology."""
    __tablename__ = "evaluation_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"))
    split: Mapped[str] = mapped_column(String(12))
    methodology: Mapped[dict] = mapped_column(JSONB)  # normalisation options + methodology version
    page_count: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class EvaluationResult(UUIDPk, Base):
    __tablename__ = "evaluation_results"

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True)
    gt_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ground_truth_pages.id", ondelete="CASCADE"), index=True)
    recognition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recognitions.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    methodology: Mapped[dict] = mapped_column(JSONB)
    alignment: Mapped[str] = mapped_column(String(12))  # line | page
    ref_chars: Mapped[int] = mapped_column(Integer)
    char_errors: Mapped[int] = mapped_column(Integer)
    ref_words: Mapped[int] = mapped_column(Integer)
    word_errors: Mapped[int] = mapped_column(Integer)
    cer: Mapped[float | None] = mapped_column(Float)
    wer: Mapped[float | None] = mapped_column(Float)
    lines_matched: Mapped[int] = mapped_column(Integer)
    lines_missing: Mapped[int] = mapped_column(Integer)  # reference lines without hypothesis
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # per-line errors, edit ops summary
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    __table_args__ = (UniqueConstraint("run_id", "gt_page_id"),)


class Comparison(UUIDPk, Base):
    """Several models run on the same page, same image and same segmentation (§12)."""
    __tablename__ = "comparisons"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="RESTRICT"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    model_version_ids: Mapped[list] = mapped_column(JSONB)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class Export(UUIDPk, Base):
    """A batch export (document or collection) produced by a worker as a ZIP archive."""
    __tablename__ = "exports"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(20))  # document | collection
    scope_id: Mapped[uuid.UUID] = mapped_column()
    formats: Mapped[list] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|completed|failed
    storage_key: Mapped[str | None] = mapped_column(String(300))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # files, validation results
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
