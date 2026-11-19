"""Current user profile, sessions, and platform user administration."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser, PlatformAdmin
from app.core.errors import ApiError, not_found
from app.models import Institution, InstitutionMembership, User, UserSession
from app.schemas import AdminUserUpdateIn, InstitutionMembershipOut, MeOut, SessionOut, UserOut, UserUpdateIn
from app.services import audit

router = APIRouter(tags=["users"])


def _me(db, user: User) -> MeOut:
    rows = db.execute(
        select(InstitutionMembership.institution_id, Institution.name, InstitutionMembership.role)
        .join(Institution).where(InstitutionMembership.user_id == user.id).order_by(Institution.name)
    ).all()
    out = MeOut.model_validate(user)
    out.institutions = [InstitutionMembershipOut(institution_id=r[0], institution_name=r[1], role=r[2]) for r in rows]
    return out


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, db: DB):
    return _me(db, user)


@router.patch("/me", response_model=MeOut)
def update_me(body: UserUpdateIn, request: Request, user: CurrentUser, db: DB):
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(user, k, v)
    audit.record(db, "user.update_profile", actor_id=user.id, request=request, entity_type="user", entity_id=user.id,
                 details={"fields": sorted(changes)})
    db.commit()
    return _me(db, user)


@router.get("/me/sessions", response_model=list[SessionOut])
def my_sessions(request: Request, user: CurrentUser, db: DB):
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        .order_by(UserSession.created_at.desc())
    ).all()
    out = []
    for r in rows:
        so = SessionOut.model_validate(r)
        so.current = r.family_id == request.state.session_family
        out.append(so)
    return out


@router.delete("/me/sessions/{family_id}", status_code=204)
def revoke_session(family_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    res = db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.family_id == family_id,
                                               UserSession.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
    if res.rowcount == 0:
        raise not_found("Session")
    audit.record(db, "auth.session_revoked", actor_id=user.id, request=request, entity_type="session", entity_id=family_id)
    db.commit()
    return Response(status_code=204)


# ---------- platform administration ----------
@router.get("/admin/users", response_model=dict, tags=["administration"])
def list_users(_: PlatformAdmin, db: DB, q: str | None = None, limit: int = Query(50, le=200), offset: int = 0):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(User.email).like(like) | func.lower(User.full_name).like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(User.created_at).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset, "items": [UserOut.model_validate(u).model_dump(mode="json") for u in rows]}


@router.patch("/admin/users/{user_id}", response_model=UserOut, tags=["administration"])
def admin_update_user(user_id: uuid.UUID, body: AdminUserUpdateIn, request: Request, admin: PlatformAdmin, db: DB):
    target = db.get(User, user_id)
    if target is None:
        raise not_found("User")
    changes = body.model_dump(exclude_unset=True)
    if target.id == admin.id and (changes.get("is_active") is False or changes.get("is_platform_admin") is False):
        raise ApiError(400, "self_lockout", "Administrators cannot deactivate or demote themselves")
    for k, v in changes.items():
        setattr(target, k, v)
    if changes.get("is_active") is False:
        db.execute(update(UserSession).where(UserSession.user_id == target.id, UserSession.revoked_at.is_(None))
                   .values(revoked_at=datetime.now(timezone.utc)))
    audit.record(db, "admin.user_update", actor_id=admin.id, request=request, entity_type="user", entity_id=target.id,
                 details=changes)
    db.commit()
    return target


# ---------- invitations (Phase 7: accounts on installations without self-registration) ----------
INVITATION_TTL_DAYS = 7


@router.post("/admin/users", response_model=UserOut, status_code=201, tags=["administration"])
def invite_user(body: dict, request: Request, admin: CurrentUser, db: DB):
    """Create an account and e-mail a single-use link to choose a password (valid 7 days).

    Platform administrators may invite anyone; institution administrators may invite into their own
    institution (``institution_id`` required). The account has no usable password until the link is used."""
    from datetime import timedelta

    from pydantic import BaseModel, EmailStr, Field, ValidationError

    from app.config import get_settings
    from app.core.permissions import institution_role
    from app.core.security import hash_password, hash_token, new_opaque_token
    from app.models import InstitutionRole, PasswordResetToken
    from app.services import mail

    class InviteIn(BaseModel):
        email: EmailStr
        full_name: str = Field(min_length=1, max_length=200)
        institution_id: uuid.UUID | None = None
        institution_role: InstitutionRole = InstitutionRole.MEMBER

    try:
        inv = InviteIn.model_validate(body)
    except ValidationError as e:
        raise ApiError(422, "validation_error", "Invalid invitation",
                       details=[{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]) from e
    if not admin.is_platform_admin:
        if inv.institution_id is None or institution_role(db, admin, inv.institution_id) != InstitutionRole.INSTITUTION_ADMIN:
            raise ApiError(403, "forbidden", "Only platform administrators, or institution administrators for their institution, can invite users")
    if inv.institution_id and db.get(Institution, inv.institution_id) is None:
        raise not_found("Institution")
    if db.scalar(select(User.id).where(func.lower(User.email) == inv.email.lower())):
        raise ApiError(409, "email_taken", "An account with this e-mail already exists")
    s = get_settings()
    user = User(email=inv.email, full_name=inv.full_name, password_hash=hash_password(new_opaque_token()))
    db.add(user)
    db.flush()
    if inv.institution_id:
        db.add(InstitutionMembership(institution_id=inv.institution_id, user_id=user.id, role=inv.institution_role))
    token = new_opaque_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(token),
                              expires_at=datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS)))
    audit.record(db, "admin.user_invited", actor_id=admin.id, request=request, entity_type="user", entity_id=user.id,
                 institution_id=inv.institution_id, details={"email": inv.email, "institution_role": inv.institution_role.value
                                                             if inv.institution_id else None})
    db.commit()
    link = f"{s.public_base_url.rstrip('/')}/reset-password?token={token}"
    mail.send_mail(user.email, "SCRIPTORIUM — invitation",
                   f"Hello {user.full_name},\n\n{admin.full_name} has created a SCRIPTORIUM account for you.\n"
                   f"Choose your password within {INVITATION_TTL_DAYS} days by opening:\n\n{link}\n")
    return user
