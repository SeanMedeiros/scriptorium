"""API schemas for documents, pages, models, jobs and recognition outputs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import JobStatus, JobType, ModelStatus, ModelTask
from app.recognition.preprocessing import Pipeline
from app.schemas import _check_dc, _check_langs


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentMetaIn(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    document_type: str | None = Field(default=None, max_length=80)
    script_type: Literal["manuscript", "print", "mixed"] = "manuscript"
    languages: list[str] = Field(default_factory=list, max_length=20)
    date_text: str | None = Field(default=None, max_length=100)
    date_start: int | None = Field(default=None, ge=-3000, le=2100)
    date_end: int | None = Field(default=None, ge=-3000, le=2100)
    archival_reference: str | None = Field(default=None, max_length=300)
    provenance: str | None = Field(default=None, max_length=20000)
    notes: str | None = Field(default=None, max_length=20000)
    dc_metadata: dict[str, str | list[str]] = Field(default_factory=dict)

    @field_validator("languages")
    @classmethod
    def _l(cls, v: list[str]) -> list[str]:
        return _check_langs(v)

    @field_validator("dc_metadata")
    @classmethod
    def _d(cls, v: dict) -> dict:
        return _check_dc(v)


class AssetOut(ORM):
    id: uuid.UUID
    kind: str
    sha256: str
    size_bytes: int
    mime_type: str
    original_filename: str | None
    width: int | None
    height: int | None
    dpi: int | None
    parent_asset_id: uuid.UUID | None
    derivation: dict
    created_at: datetime
    url: str | None = None


class PageOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    sequence: int
    label: str | None
    source_page_number: int | None
    image_asset_id: uuid.UUID
    image: AssetOut | None = None
    latest_job_status: JobStatus | None = None
    recognition_count: int = 0


class DocumentOut(ORM):
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    description: str | None
    document_type: str | None
    script_type: str
    languages: list[str]
    date_text: str | None
    date_start: int | None
    date_end: int | None
    archival_reference: str | None
    provenance: str | None
    notes: str | None
    dc_metadata: dict
    ingest_status: str
    ingest_error: dict | None
    source_asset_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    page_count: int = 0
    source: AssetOut | None = None
    project_id: uuid.UUID | None = None
    project_title: str | None = None
    collection_title: str | None = None


class DocumentDetailOut(DocumentOut):
    pages: list[PageOut] = []


class ModelVersionOut(ORM):
    id: uuid.UUID
    model_id: uuid.UUID
    model_slug: str
    model_name: str
    task: ModelTask
    source: str
    version: str
    doi: str | None
    license_id: str | None
    status: ModelStatus
    sha256: str | None
    source_checksum: str | None
    size_bytes: int | None
    unicode_normalization: str | None
    kraken_version_validated: str | None
    installed_at: datetime | None
    validated_at: datetime | None
    is_pinned: bool


class ModelVersionDetailOut(ModelVersionOut):
    record_id: str | None = None
    publication_date: str | None = None
    bytes_downloaded: int = 0
    license_accepted_at: datetime | None = None
    model_card_format: str | None = None
    model_card: dict = {}
    compatibility: dict = {}
    source_metadata: dict
    file_metadata: dict
    install_log: list


class JobCreateIn(BaseModel):
    job_type: JobType = JobType.SEGMENT_RECOGNIZE
    page_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    recognition_model_version_id: uuid.UUID | None = None
    segmentation_model_version_id: uuid.UUID | None = None  # default: bundled blla
    input_segmentation_id: uuid.UUID | None = None  # for job_type=recognize on a single page
    preprocessing: Pipeline = Field(default_factory=Pipeline)
    text_direction: Literal["horizontal-lr", "horizontal-rl", "vertical-lr", "vertical-rl"] = "horizontal-lr"


class JobOut(ORM):
    id: uuid.UUID
    batch_id: uuid.UUID | None
    project_id: uuid.UUID
    document_id: uuid.UUID
    page_id: uuid.UUID
    job_type: JobType
    segmentation_model_version_id: uuid.UUID | None
    recognition_model_version_id: uuid.UUID | None
    input_segmentation_id: uuid.UUID | None
    config: dict
    status: JobStatus
    progress: float
    stage: str | None
    submitted_by_id: uuid.UUID | None
    submitted_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    attempts: int
    retry_of_id: uuid.UUID | None
    worker: str | None
    error: dict | None
    output_segmentation_id: uuid.UUID | None
    output_recognition_id: uuid.UUID | None
    document_title: str | None = None
    page_sequence: int | None = None
    recognition_model: str | None = None


class JobDetailOut(JobOut):
    log: list


class BatchOut(BaseModel):
    batch_id: uuid.UUID
    jobs: list[JobOut]


class RegionOut(ORM):
    id: uuid.UUID
    external_id: str | None
    region_type: str
    order_index: int
    boundary: list


class LineOut(ORM):
    id: uuid.UUID
    region_id: uuid.UUID | None
    external_id: str | None
    order_index: int
    baseline: list | None
    boundary: list | None
    bbox: list


class SegmentationOut(ORM):
    id: uuid.UUID
    page_id: uuid.UUID
    job_id: uuid.UUID | None
    source: str
    model_version_id: uuid.UUID | None
    input_asset_id: uuid.UUID
    text_direction: str
    config: dict
    software: dict
    duration_ms: int | None
    created_at: datetime
    input_asset: AssetOut | None = None
    regions: list[RegionOut] = []
    lines: list[LineOut] = []


class LineRecognitionOut(ORM):
    line_id: uuid.UUID
    order_index: int
    text: str
    mean_confidence: float | None
    confidences: list


class RecognitionOut(ORM):
    id: uuid.UUID
    segmentation_id: uuid.UUID
    page_id: uuid.UUID
    job_id: uuid.UUID | None
    model_version_id: uuid.UUID
    input_asset_id: uuid.UUID
    config: dict
    software: dict
    duration_ms: int | None
    line_count: int
    char_count: int
    created_at: datetime
    model: str | None = None
    unicode_normalization: str | None = None
    confidence_note: str = ("mean_confidence is the average of per-character softmax maxima reported by the model; "
                            "it is NOT a calibrated probability nor a measured accuracy.")
    lines: list[LineRecognitionOut] = []
