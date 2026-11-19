from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, forbidden, not_found
from app.core.permissions import ProjectPermission, project_permissions
from app.core.security import decode_access_token
from app.db import get_db
from app.logging_setup import user_id_var
from app.models import Project, User, UserSession

DB = Annotated[Session, Depends(get_db)]


def _unauthorized(msg: str = "Authentication required") -> ApiError:
    return ApiError(401, "unauthorized", msg, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, db: DB) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _unauthorized()
    try:
        payload = decode_access_token(auth[7:].strip())
        user_id = uuid.UUID(payload["sub"])
        family = uuid.UUID(payload["sid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized("Invalid or expired access token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Account inactive or not found")
    # Access tokens die with their session: logout / revocation is immediate.
    alive = db.scalar(
        select(UserSession.id).where(UserSession.family_id == family, UserSession.revoked_at.is_(None)).limit(1)
    )
    if alive is None:
        raise _unauthorized("Session revoked")
    request.state.session_family = family
    user_id_var.set(str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_platform_admin(user: CurrentUser) -> User:
    if not user.is_platform_admin:
        raise forbidden("Platform administrator role required")
    return user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]


def load_project(db: Session, user: User, project_id: uuid.UUID, needed: ProjectPermission) -> tuple[Project, frozenset]:
    project = db.get(Project, project_id)
    if project is None:
        raise not_found("Project")
    perms = project_permissions(db, user, project)
    if ProjectPermission.VIEW not in perms:
        # Do not reveal the existence of other institutions' projects.
        raise not_found("Project")
    if needed not in perms:
        raise forbidden()
    return project, perms
