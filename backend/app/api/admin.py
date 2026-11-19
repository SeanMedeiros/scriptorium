"""Platform administration endpoints (Phase 7): retention runs and security configuration status."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import DB, PlatformAdmin
from app.config import get_settings
from app.services import audit, retention

router = APIRouter(prefix="/admin", tags=["administration"])


@router.post("/retention")
def run_retention(request: Request, user: PlatformAdmin, db: DB, apply: bool = False):
    """Dry run by default (`apply=false`): reports what the retention policy would remove."""
    report = retention.run(db, apply=apply, actor_id=user.id)
    if not apply:
        audit.record(db, "retention.dry_run", actor_id=user.id, request=request, entity_type="system",
                     details={k: report[k] for k in ("assets_removed", "objects_removed", "bytes_freed", "exports_expired_count")})
        db.commit()
    return report


@router.get("/security-status")
def security_status(user: PlatformAdmin):
    """Security-relevant configuration, for institutional deployment reviews (no secrets disclosed)."""
    s = get_settings()
    return {
        "environment": s.environment,
        "cookie_secure": s.cookie_secure,
        "hsts_seconds": s.hsts_seconds,
        "malware_scanner": s.malware_scanner,
        "clamd_address": s.clamd_address if s.malware_scanner == "clamd" else None,
        "allow_self_registration": s.allow_self_registration,
        "trust_forwarded_for": s.trust_forwarded_for,
        "cors_origins": s.cors_origins,
        "max_upload_bytes": s.max_upload_bytes,
        "export_retention_days": s.export_retention_days,
        "orphan_grace_days": s.orphan_grace_days,
        "storage_backend": s.storage_backend,
        "secret_key_is_default": s.secret_key.startswith("dev-insecure"),
    }
