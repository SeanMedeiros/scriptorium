"""Documents, pages and immutable image assets."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamps, UUIDPk, utcnow
from app.models.core import _enum


class AssetKind(str, enum.Enum):
    ORIGINAL = "original"  # uploaded file exactly as received (image or PDF)
    PAGE_IMAGE = "page_image"  # page raster extracted from an original (e.g. PDF page)
    DERIVED = "derived"  # result of a recorded preprocessing pipeline


class ImageAsset(UUIDPk, Base):
    """A stored binary. Never modified: derivations create new assets that point to their parent."""

    __tablename__ = "image_assets"

    kind: Mapped[AssetKind] = mapped_column(_enum(AssetKind, "asset_kind"))
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(100))
    original_filename: Mapped[str | None] = mapped_column(String(500))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    dpi: Mapped[int | None] = mapped_column(Integer)
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    # Exact, reproducible description of how this asset was produced from its parent.
    derivation: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


class Document(UUIDPk, Timestamps, Base):
    __tablename__ = "documents"

    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(String(80))  # letter, notarial deed, register…
    script_type: Mapped[str] = mapped_column(String(20), default="manuscript", server_default="manuscript")  # manuscript|print|mixed
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    date_text: Mapped[str | None] = mapped_column(String(100))  # as written, e.g. "1622-03-04" or "c. 1650"
    date_start: Mapped[int | None] = mapped_column(Integer)
    date_end: Mapped[int | None] = mapped_column(Integer)
    archival_reference: Mapped[str | None] = mapped_column(String(300))
    provenance: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    dc_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    ingest_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")  # pending|ready|failed
    ingest_error: Mapped[dict | None] = mapped_column(JSONB)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    pages: Mapped[list[Page]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="Page.sequence")

    __table_args__ = (
        CheckConstraint("script_type IN ('manuscript','print','mixed')", name="script_type"),
        CheckConstraint("ingest_status IN ('pending','ready','failed')", name="ingest_status"),
    )


class Page(UUIDPk, Timestamps, Base):
    __tablename__ = "pages"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(50))  # folio / page label, e.g. "12v"
    image_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("image_assets.id", ondelete="RESTRICT"), index=True)
    source_page_number: Mapped[int | None] = mapped_column(Integer)  # page number inside a source PDF

    document: Mapped[Document] = relationship(back_populates="pages")
    image: Mapped[ImageAsset] = relationship(foreign_keys=[image_asset_id])

    __table_args__ = (UniqueConstraint("document_id", "sequence", name="uq_page_sequence"), Index("ix_pages_document", "document_id"))
