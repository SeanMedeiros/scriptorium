"""Models, jobs and immutable recognition outputs.

Outputs (segmentations, regions, lines, recognitions, line recognitions) are
protected against UPDATE by database triggers: an original HTR result can be
superseded by a new run or a human correction, never rewritten.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamps, UUIDPk, utcnow
from app.models.core import _enum


class ModelTask(str, enum.Enum):
    RECOGNITION = "recognition"
    SEGMENTATION = "segmentation"


class ModelSource(str, enum.Enum):
    BUNDLED = "bundled"  # shipped inside the kraken package
    ZENODO = "zenodo"
    MANUAL = "manual"
    TRAINED = "trained"  # produced on this platform by a training job


class ModelStatus(str, enum.Enum):
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    INSTALLED = "installed"
    VALIDATED = "validated"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"
    UPDATE_AVAILABLE = "update_available"


class ModelLicense(Base):
    """Licences as declared by model sources (Zenodo licence ids, SPDX-like)."""

    __tablename__ = "model_licenses"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(100))  # where the licence id was read
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class RecognitionModel(UUIDPk, Timestamps, Base):
    """A model family entry (e.g. TraPrInq, CATMuS Medieval). One record per Zenodo concept."""

    __tablename__ = "recognition_models"

    slug: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    task: Mapped[ModelTask] = mapped_column(_enum(ModelTask, "model_task"))
    source: Mapped[ModelSource] = mapped_column(_enum(ModelSource, "model_source"))
    concept_doi: Mapped[str | None] = mapped_column(String(120))
    concept_record_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    family: Mapped[str | None] = mapped_column(String(60))
    # Version used when a job names the model but not a version. Changed only explicitly (pin / rollback).
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL", use_alter=True, name="fk_recognition_models_active_version"))
    # Facts declared by the source (model card / record), copied verbatim.
    source_languages: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    source_scripts: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # Attributes entered by an administrator, always displayed as curated, never as source facts.
    curated: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    latest_record_id: Mapped[str | None] = mapped_column(String(40))
    latest_version_label: Mapped[str | None] = mapped_column(String(60))
    update_available: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_update_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Institutional models: usable only inside their owner scope. NULL = platform-wide model.
    owner_institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("institutions.id", ondelete="RESTRICT"), index=True)
    owner_project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)

    versions: Mapped[list[ModelVersion]] = relationship(back_populates="model", order_by="ModelVersion.created_at",
                                                        foreign_keys="ModelVersion.model_id")


class ModelVersion(UUIDPk, Timestamps, Base):
    __tablename__ = "model_versions"

    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recognition_models.id", ondelete="RESTRICT"), index=True)
    version: Mapped[str] = mapped_column(String(60))
    doi: Mapped[str | None] = mapped_column(String(120))
    record_id: Mapped[str | None] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str | None] = mapped_column(String(1000))  # absolute path once installed
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    source_checksum: Mapped[str | None] = mapped_column(String(100))  # as published by the source, e.g. "md5:…"
    status: Mapped[ModelStatus] = mapped_column(_enum(ModelStatus, "model_status"))
    license_id: Mapped[str | None] = mapped_column(ForeignKey("model_licenses.id", ondelete="RESTRICT"))
    license_accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    license_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_date: Mapped[str | None] = mapped_column(String(20))
    download_url: Mapped[str | None] = mapped_column(String(1000))
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    model_card_format: Mapped[str | None] = mapped_column(String(20))
    model_card: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # {format, valid, errors, card}
    compatibility: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # pre-download assessment
    installed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Verbatim metadata from the source and from the model file itself. Never invented.
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    file_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    unicode_normalization: Mapped[str | None] = mapped_column(String(10))  # NFD, NFC… as documented by source
    kraken_version_validated: Mapped[str | None] = mapped_column(String(30))
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    install_log: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # Trained / derived models: base model, training job, dataset snapshot and configuration.
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"))
    training_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("training_jobs.id", ondelete="RESTRICT", use_alter=True, name="fk_model_versions_training_job"))
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    model: Mapped[RecognitionModel] = relationship(back_populates="versions", foreign_keys=[model_id])

    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version"),)


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, enum.Enum):
    SEGMENT = "segment"  # layout analysis only
    RECOGNIZE = "recognize"  # recognise an existing segmentation
    SEGMENT_RECOGNIZE = "segment_recognize"


class RecognitionJob(UUIDPk, Base):
    __tablename__ = "recognition_jobs"

    batch_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    job_type: Mapped[JobType] = mapped_column(_enum(JobType, "job_type"))
    segmentation_model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    recognition_model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    input_segmentation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segmentations.id", ondelete="SET NULL", use_alter=True, name="fk_recognition_jobs_input_segmentation_id"), index=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    stage: Mapped[str | None] = mapped_column(String(40))
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recognition_jobs.id", ondelete="SET NULL"), index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    worker: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[dict | None] = mapped_column(JSONB)
    log: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    output_segmentation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segmentations.id", ondelete="SET NULL", use_alter=True, name="fk_recognition_jobs_output_segmentation_id"), index=True)
    output_recognition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recognitions.id", ondelete="SET NULL", use_alter=True, name="fk_recognition_jobs_output_recognition_id"), index=True)

    __table_args__ = (Index("ix_jobs_status_submitted", "status", "submitted_at"),)


class Segmentation(UUIDPk, Base):
    """Layout analysis of one page: regions + lines in reading order. Immutable."""

    __tablename__ = "segmentations"

    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recognition_jobs.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # kraken | manual | import
    parent_segmentation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segmentations.id", ondelete="SET NULL"), index=True)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    input_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    text_direction: Mapped[str] = mapped_column(String(20), default="horizontal-lr")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    software: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # kraken/torch versions
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    regions: Mapped[list[TextRegion]] = relationship(back_populates="segmentation", order_by="TextRegion.order_index")
    lines: Mapped[list[TextLine]] = relationship(back_populates="segmentation", order_by="TextLine.order_index")


class TextRegion(UUIDPk, Base):
    __tablename__ = "text_regions"

    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100))  # id assigned by kraken
    region_type: Mapped[str] = mapped_column(String(60))
    order_index: Mapped[int] = mapped_column(Integer)
    boundary: Mapped[list] = mapped_column(JSONB)  # [[x, y], …] in page-image pixel coordinates
    tags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    segmentation: Mapped[Segmentation] = relationship(back_populates="regions")


class TextLine(UUIDPk, Base):
    __tablename__ = "text_lines"

    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="CASCADE"), index=True)
    region_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("text_regions.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100))
    order_index: Mapped[int] = mapped_column(Integer)
    baseline: Mapped[list | None] = mapped_column(JSONB)
    boundary: Mapped[list | None] = mapped_column(JSONB)
    bbox: Mapped[list] = mapped_column(JSONB)  # [x0, y0, x1, y1]
    tags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    segmentation: Mapped[Segmentation] = relationship(back_populates="lines")

    __table_args__ = (UniqueConstraint("segmentation_id", "order_index", name="uq_line_order"),)


class Recognition(UUIDPk, Base):
    """One HTR pass of one model over one segmentation. Immutable original output."""

    __tablename__ = "recognitions"

    segmentation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segmentations.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recognition_jobs.id", ondelete="SET NULL"), index=True)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True)
    input_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    software: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    lines: Mapped[list[LineRecognition]] = relationship(back_populates="recognition", order_by="LineRecognition.order_index")


class LineRecognition(UUIDPk, Base):
    __tablename__ = "line_recognitions"

    recognition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recognitions.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("text_lines.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # exact Unicode sequence produced by the model (no normalisation)
    # Per-character softmax maxima reported by the model. NOT calibrated probabilities.
    confidences: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    mean_confidence: Mapped[float | None] = mapped_column(Float)
    cuts: Mapped[list | None] = mapped_column(JSONB)

    recognition: Mapped[Recognition] = relationship(back_populates="lines")

    __table_args__ = (UniqueConstraint("recognition_id", "line_id", name="uq_linerec_line"),)
