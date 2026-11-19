from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, PlatformAdmin
from app.core.errors import ApiError, forbidden, not_found
from app.core.permissions import institution_role
from app.models import Institution, InstitutionMembership, InstitutionRole, User
from app.schemas import InstitutionIn, InstitutionMemberIn, InstitutionMemberOut, InstitutionOut
from app.services import audit

router = APIRouter(prefix="/institutions", tags=["institutions"])


def _get(db, user: User, institution_id: uuid.UUID, admin: bool = False) -> Institution:
    inst = db.get(Institution, institution_id)
    if inst is None:
        raise not_found("Institution")
    role = institution_role(db, user, inst.id)
    if not user.is_platform_admin:
        if role is None:
            raise not_found("Institution")
        if admin and role != InstitutionRole.INSTITUTION_ADMIN:
            raise forbidden("Institution administrator role required")
    return inst


@router.get("", response_model=list[InstitutionOut])
def list_institutions(user: CurrentUser, db: DB):
    stmt = select(Institution).order_by(Institution.name)
    if not user.is_platform_admin:
        stmt = stmt.join(InstitutionMembership).where(InstitutionMembership.user_id == user.id)
    return db.scalars(stmt).all()


@router.post("", response_model=InstitutionOut, status_code=201)
def create_institution(body: InstitutionIn, request: Request, admin: PlatformAdmin, db: DB):
    inst = Institution(**body.model_dump())
    db.add(inst)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "slug_taken", "An institution with this slug already exists")
    audit.record(db, "institution.create", actor_id=admin.id, request=request, entity_type="institution",
                 entity_id=inst.id, institution_id=inst.id, details={"name": inst.name})
    db.commit()
    return inst


@router.get("/{institution_id}", response_model=InstitutionOut)
def get_institution(institution_id: uuid.UUID, user: CurrentUser, db: DB):
    return _get(db, user, institution_id)


@router.get("/{institution_id}/members", response_model=list[InstitutionMemberOut])
def list_members(institution_id: uuid.UUID, user: CurrentUser, db: DB):
    _get(db, user, institution_id)
    rows = db.execute(
        select(User.id, User.email, User.full_name, InstitutionMembership.role).join(InstitutionMembership)
        .where(InstitutionMembership.institution_id == institution_id).order_by(User.full_name)
    ).all()
    return [InstitutionMemberOut(user_id=r[0], email=r[1], full_name=r[2], role=r[3]) for r in rows]


@router.put("/{institution_id}/members", response_model=InstitutionMemberOut)
def upsert_member(institution_id: uuid.UUID, body: InstitutionMemberIn, request: Request, user: CurrentUser, db: DB):
    _get(db, user, institution_id, admin=True)
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if target is None:
        raise ApiError(404, "user_not_found", "No account with this e-mail; the researcher must register first")
    m = db.scalar(select(InstitutionMembership).where(InstitutionMembership.institution_id == institution_id,
                                                      InstitutionMembership.user_id == target.id))
    if m is None:
        m = InstitutionMembership(institution_id=institution_id, user_id=target.id, role=body.role)
        db.add(m)
    else:
        m.role = body.role
    audit.record(db, "institution.member_set", actor_id=user.id, request=request, entity_type="user", entity_id=target.id,
                 institution_id=institution_id, details={"role": body.role.value})
    db.commit()
    return InstitutionMemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/{institution_id}/members/{user_id}", status_code=204)
def remove_member(institution_id: uuid.UUID, user_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    _get(db, user, institution_id, admin=True)
    m = db.scalar(select(InstitutionMembership).where(InstitutionMembership.institution_id == institution_id,
                                                      InstitutionMembership.user_id == user_id))
    if m is None:
        raise not_found("Membership")
    if m.role == InstitutionRole.INSTITUTION_ADMIN:
        admins = db.scalar(select(func.count(InstitutionMembership.id)).where(InstitutionMembership.institution_id == institution_id,
                                                      InstitutionMembership.role == InstitutionRole.INSTITUTION_ADMIN))
        if admins <= 1:
            raise ApiError(400, "last_admin", "An institution must keep at least one administrator")
    db.delete(m)
    audit.record(db, "institution.member_removed", actor_id=user.id, request=request, entity_type="user", entity_id=user_id,
                 institution_id=institution_id)
    db.commit()
    return Response(status_code=204)


# --- Storage policy and usage (Phase 7) ------------------------------------------------------

@router.get("/{institution_id}/storage")
def get_storage_policy(institution_id: uuid.UUID, user: CurrentUser, db: DB):
    """Policy and effective limits for members; usage figures for institution administrators."""
    from app.services import retention

    inst = _get(db, user, institution_id)
    is_admin = user.is_platform_admin or institution_role(db, user, inst.id) == InstitutionRole.INSTITUTION_ADMIN
    out = {"policy": retention.policy_of(inst).model_dump(), "effective": retention.effective_limits(inst)}
    if is_admin:
        out["usage"] = retention.usage(db, inst)
    return out


@router.put("/{institution_id}/storage-policy")
def put_storage_policy(institution_id: uuid.UUID, body: dict, request: Request, user: CurrentUser, db: DB):
    from pydantic import ValidationError

    from app.config import get_settings
    from app.services import retention

    inst = _get(db, user, institution_id, admin=True)
    try:
        pol = retention.StoragePolicy.model_validate(body)
    except ValidationError as e:
        raise ApiError(422, "invalid_storage_policy", "Invalid storage policy",
                       details=[{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]) from e
    s = get_settings()
    if pol.max_upload_bytes and pol.max_upload_bytes > s.max_upload_bytes:
        raise ApiError(422, "invalid_storage_policy",
                       f"max_upload_bytes cannot exceed the platform limit ({s.max_upload_bytes} bytes)")
    before = inst.storage_policy or {}
    inst.storage_policy = pol.model_dump(exclude_none=True)
    audit.record(db, "institution.storage_policy_changed", actor_id=user.id, request=request, entity_type="institution",
                 entity_id=inst.id, institution_id=inst.id, details={"before": before, "after": inst.storage_policy})
    db.commit()
    return get_storage_policy(institution_id, user, db)
