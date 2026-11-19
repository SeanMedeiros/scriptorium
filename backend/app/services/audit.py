"""Audit trail writer. Records are added to the caller's transaction."""
from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import request_id_var
from app.models import AuditLog


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request | None) -> str | None:
    """Client address. X-Forwarded-For is honoured only behind a trusted reverse proxy
    (SCRIPTORIUM_TRUST_FORWARDED_FOR=true); otherwise it could be spoofed into the audit trail."""
    if request is None:
        return None
    if get_settings().trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for")
        if fwd and (ip := _valid_ip(fwd.split(",")[0])):
            return ip
    return _valid_ip(request.client.host if request.client else None)


def record(
    db: Session,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    institution_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        institution_id=institution_id,
        project_id=project_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent", "")[:500] if request else None),
        request_id=request_id_var.get(),
        details=details or {},
    )
    db.add(entry)
    return entry
