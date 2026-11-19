"""Data retention and institutional storage policies (Phase 7).

Institution ``storage_policy`` (JSONB, validated by :class:`StoragePolicy`):

* ``max_storage_bytes``      quota for uploaded originals across the institution's projects.
* ``max_upload_bytes``       per-file upload limit (cannot exceed the platform limit).
* ``allowed_upload_types``   subset of png/jpeg/tiff/pdf accepted for upload.
* ``export_retention_days``  batch-export archives are deleted after N days (0 = keep).
* ``note``                   free-text statement shown to members (e.g. where data is hosted).

Projects without an institution use the platform defaults (``SCRIPTORIUM_*`` settings).

The retention run (``python -m app.cli retention``, the Celery beat task ``maintenance.retention``
or ``POST /api/v1/admin/retention``) performs, in order:

1. **Export expiry** — completed batch exports older than the applicable retention period lose
   their archive (the database row, its manifest summary and the audit trail are kept; status
   becomes ``expired``).
2. **Unreferenced asset rows** — image assets no longer referenced by any document, page,
   segmentation, recognition or child asset (e.g. after a document was deleted) and older than
   the grace period are removed, children first.
3. **Orphan objects** — storage objects whose key is not referenced by any database row and that
   are older than the grace period are deleted. The grace period protects uploads in flight.

Research data that is still referenced is never touched. Immutable research records (recognitions,
revisions, ground truth, evaluation results) are not subject to retention. The audit trail is
append-only and is retained indefinitely (export it with the database backup).
Every run returns a report and, when applied, writes an audit record.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Collection, Document, Export, ImageAsset, Institution, Project
from app.services import audit
from app.storage import get_storage

log = logging.getLogger("scriptorium.retention")

UploadType = Literal["png", "jpeg", "tiff", "pdf"]
_MIME = {"png": "image/png", "jpeg": "image/jpeg", "tiff": "image/tiff", "pdf": "application/pdf"}


class StoragePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_storage_bytes: int | None = Field(None, ge=1)
    max_upload_bytes: int | None = Field(None, ge=1)
    allowed_upload_types: list[UploadType] | None = None
    export_retention_days: int | None = Field(None, ge=0, le=36500)
    note: str | None = Field(None, max_length=2000)

    @field_validator("allowed_upload_types")
    @classmethod
    def _non_empty(cls, v):
        if v is not None and not v:
            raise ValueError("allowed_upload_types must list at least one type (omit it to allow all)")
        return sorted(set(v)) if v else v


def policy_of(inst: Institution | None) -> StoragePolicy:
    return StoragePolicy.model_validate(inst.storage_policy or {}) if inst else StoragePolicy()


def effective_limits(inst: Institution | None) -> dict:
    s = get_settings()
    p = policy_of(inst)
    return {
        "max_upload_bytes": min(p.max_upload_bytes or s.max_upload_bytes, s.max_upload_bytes),
        "allowed_mime_types": sorted(_MIME[t] for t in (p.allowed_upload_types or _MIME)),
        "max_storage_bytes": p.max_storage_bytes,
        "export_retention_days": p.export_retention_days if p.export_retention_days is not None else s.export_retention_days,
    }


def originals_usage(db: Session, institution_id) -> int:
    """Bytes of distinct uploaded originals held by the institution's projects."""
    sub = (select(ImageAsset.storage_key, func.max(ImageAsset.size_bytes).label("b"))
           .join(Document, Document.source_asset_id == ImageAsset.id)
           .join(Collection, Collection.id == Document.collection_id)
           .join(Project, Project.id == Collection.project_id)
           .where(Project.institution_id == institution_id)
           .group_by(ImageAsset.storage_key).subquery())
    return int(db.scalar(select(func.coalesce(func.sum(sub.c.b), 0))))


def usage(db: Session, inst: Institution) -> dict:
    proj = select(Project.id).where(Project.institution_id == inst.id)
    docs = (select(func.count(Document.id)).join(Collection, Collection.id == Document.collection_id)
            .where(Collection.project_id.in_(proj)))
    exports = select(func.coalesce(func.sum(Export.size_bytes), 0), func.count(Export.id)).where(
        Export.project_id.in_(proj), Export.storage_key.is_not(None))
    eb, ec = db.execute(exports).one()
    return {"originals_bytes": originals_usage(db, inst.id), "documents": int(db.scalar(docs)),
            "projects": int(db.scalar(select(func.count()).select_from(proj.subquery()))),
            "export_archives": int(ec), "export_bytes": int(eb)}


# ---------------------------------------------------------------------------------------------
# Retention run
# ---------------------------------------------------------------------------------------------

def _referenced_keys(db: Session) -> set[str]:
    keys: set[str] = set(db.scalars(select(ImageAsset.storage_key)))
    keys |= {k for k in db.scalars(select(Export.storage_key)) if k}
    keys |= {k for k in db.execute(text("SELECT package_key FROM model_publications WHERE package_key IS NOT NULL")).scalars()}
    return keys


_ASSET_REFS = """
SELECT a.id FROM image_assets a
WHERE a.created_at < :cutoff
  AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.source_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.image_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM segmentations s WHERE s.input_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM recognitions r WHERE r.input_asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM image_assets c WHERE c.parent_asset_id = a.id)
"""


def run(db: Session, *, apply: bool = False, now: datetime | None = None, actor_id=None) -> dict:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    grace_cutoff = now - timedelta(days=s.orphan_grace_days)
    storage = get_storage()
    report: dict = {"applied": apply, "at": now.isoformat(), "grace_days": s.orphan_grace_days,
                    "exports_expired": [], "assets_removed": 0, "objects_removed": 0, "bytes_freed": 0}

    # 1. Export archives past their retention period.
    insts = {i.id: i for i in db.scalars(select(Institution))}
    rows = db.execute(select(Export, Project.institution_id).join(Project, Project.id == Export.project_id)
                      .where(Export.status == "completed", Export.storage_key.is_not(None))).all()
    expired_keys: list[str] = []
    for exp, inst_id in rows:
        days = effective_limits(insts.get(inst_id))["export_retention_days"]
        if days and exp.created_at < now - timedelta(days=days):
            report["exports_expired"].append({"id": str(exp.id), "project_id": str(exp.project_id),
                                              "created_at": exp.created_at.isoformat(), "retention_days": days,
                                              "bytes": exp.size_bytes})
            if apply:
                expired_keys.append(exp.storage_key)
                exp.summary = {**(exp.summary or {}), "expired_at": now.isoformat(), "expired_storage_key": exp.storage_key,
                               "retention_days": days}
                exp.storage_key, exp.status = None, "expired"
    if apply:
        db.flush()

    # 2. Unreferenced image-asset rows (iteratively: removing a child may orphan its parent).
    while True:
        ids = list(db.execute(text(_ASSET_REFS), {"cutoff": grace_cutoff}).scalars())
        if not ids:
            break
        report["assets_removed"] += len(ids)
        if not apply:
            break  # dry run: report the first layer only
        db.execute(text("DELETE FROM image_assets WHERE id = ANY(:ids)"), {"ids": ids})
        db.flush()

    # 3. Objects no row references any more (computed after steps 1–2 within the transaction).
    referenced = _referenced_keys(db)
    to_delete: list[tuple[str, int]] = []
    for key, size, mtime in storage.iter_objects():
        if key in referenced:
            continue
        if datetime.fromtimestamp(mtime, timezone.utc) >= grace_cutoff and key not in expired_keys:
            continue
        to_delete.append((key, size))
    report["objects_removed"] = len(to_delete)
    report["bytes_freed"] = sum(sz for _, sz in to_delete)
    report["exports_expired_count"] = len(report["exports_expired"])

    if apply:
        audit.record(db, "retention.run", actor_id=actor_id, entity_type="system",
                     details={k: v for k, v in report.items() if k != "exports_expired"}
                     | {"export_ids": [e["id"] for e in report["exports_expired"]]})
        db.commit()  # rows first: an object is only deleted once nothing in the database points to it
        for key, _ in to_delete:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 — retried by the next run
                log.exception("could not delete storage object", extra={"key": key})
        log.info("retention run", extra={k: report[k] for k in ("assets_removed", "objects_removed", "bytes_freed")})
    else:
        db.rollback()
    report["objects"] = [k for k, _ in to_delete[:200]]
    return report
