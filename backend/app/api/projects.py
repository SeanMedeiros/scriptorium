"""Research projects, project membership and collections."""
from __future__ import annotations

import re
import unicodedata
import uuid

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, forbidden, not_found
from app.core.permissions import ProjectPermission as P
from app.core.permissions import institution_role, project_permissions, visible_projects_query
from app.models import Collection, Institution, Project, ProjectMembership, User
from app.schemas import (
    CollectionIn,
    CollectionOut,
    CollectionUpdateIn,
    ProjectIn,
    ProjectMemberIn,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdateIn,
)
from app.services import audit

router = APIRouter(prefix="/projects", tags=["projects"])


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t or "project")[:100]


def _out(db, user: User, p: Project, perms=None) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.institution_name = p.institution.name if p.institution else None
    out.owner_name = p.owner.full_name if p.owner else None
    out.collection_count = db.scalar(select(func.count(Collection.id)).where(Collection.project_id == p.id)) or 0
    out.member_count = 1 + (db.scalar(select(func.count(ProjectMembership.id)).where(
        ProjectMembership.project_id == p.id, ProjectMembership.user_id != p.owner_id)) or 0)
    out.permissions = sorted(x.value for x in (perms if perms is not None else project_permissions(db, user, p)))
    return out


@router.get("", response_model=dict)
def list_projects(user: CurrentUser, db: DB, q: str | None = None, institution_id: uuid.UUID | None = None,
                  include_archived: bool = False, limit: int = Query(50, le=200), offset: int = 0):
    stmt = visible_projects_query(user)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Project.title).like(like) | func.lower(func.coalesce(Project.description, "")).like(like))
    if institution_id:
        stmt = stmt.where(Project.institution_id == institution_id)
    if not include_archived:
        stmt = stmt.where(Project.is_archived.is_(False))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Project.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "items": [_out(db, user, p).model_dump(mode="json") for p in rows]}


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, request: Request, user: CurrentUser, db: DB):
    if body.institution_id is not None:
        if db.get(Institution, body.institution_id) is None:
            raise not_found("Institution")
        if not user.is_platform_admin and institution_role(db, user, body.institution_id) is None:
            raise forbidden("You are not a member of this institution")
    if body.period_start is not None and body.period_end is not None and body.period_start > body.period_end:
        raise ApiError(422, "invalid_period", "period_start must be <= period_end")
    base = slugify(body.title)
    slug, n = base, 2
    while db.scalar(select(Project.id).where(Project.owner_id == user.id, Project.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    p = Project(owner_id=user.id, slug=slug, **body.model_dump())
    db.add(p)
    db.flush()
    db.add(ProjectMembership(project_id=p.id, user_id=user.id, role="principal_investigator"))
    audit.record(db, "project.create", actor_id=user.id, request=request, entity_type="project", entity_id=p.id,
                 project_id=p.id, institution_id=p.institution_id, details={"title": p.title})
    db.commit()
    db.refresh(p)
    return _out(db, user, p)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, user: CurrentUser, db: DB):
    p, perms = load_project(db, user, project_id, P.VIEW)
    return _out(db, user, p, perms)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectUpdateIn, request: Request, user: CurrentUser, db: DB):
    p, perms = load_project(db, user, project_id, P.EDIT_METADATA)
    changes = body.model_dump(exclude_unset=True)
    start = changes.get("period_start", p.period_start)
    end = changes.get("period_end", p.period_end)
    if start is not None and end is not None and start > end:
        raise ApiError(422, "invalid_period", "period_start must be <= period_end")
    for k, v in changes.items():
        setattr(p, k, v)
    audit.record(db, "project.update", actor_id=user.id, request=request, entity_type="project", entity_id=p.id,
                 project_id=p.id, institution_id=p.institution_id, details={"fields": sorted(changes)})
    db.commit()
    return _out(db, user, p, perms)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.DELETE)
    if db.scalar(select(func.count(Collection.id)).where(Collection.project_id == p.id)):
        raise ApiError(409, "project_not_empty", "Delete or move the collections first, or archive the project instead")
    audit.record(db, "project.delete", actor_id=user.id, request=request, entity_type="project", entity_id=p.id,
                 institution_id=p.institution_id, details={"title": p.title})
    db.delete(p)
    db.commit()
    return Response(status_code=204)


# ---------- members ----------
@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
def list_project_members(project_id: uuid.UUID, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.VIEW)
    rows = db.execute(select(User.id, User.email, User.full_name, ProjectMembership.role).join(ProjectMembership)
                      .where(ProjectMembership.project_id == p.id).order_by(User.full_name)).all()
    out = []
    for r in rows:
        out.append(ProjectMemberOut(user_id=r[0], email=r[1], full_name=r[2], role="owner" if r[0] == p.owner_id else r[3]))
    return out


@router.put("/{project_id}/members", response_model=ProjectMemberOut)
def set_project_member(project_id: uuid.UUID, body: ProjectMemberIn, request: Request, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.MANAGE_MEMBERS)
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if target is None:
        raise ApiError(404, "user_not_found", "No account with this e-mail; the researcher must register first")
    if target.id == p.owner_id:
        raise ApiError(400, "owner_role_fixed", "The project owner always holds full permissions")
    if p.institution_id and institution_role(db, target, p.institution_id) is None:
        raise ApiError(400, "not_institution_member",
                       "Institutional projects can only include members of the institution")
    m = db.scalar(select(ProjectMembership).where(ProjectMembership.project_id == p.id, ProjectMembership.user_id == target.id))
    if m is None:
        db.add(ProjectMembership(project_id=p.id, user_id=target.id, role=body.role))
    else:
        m.role = body.role
    audit.record(db, "project.member_set", actor_id=user.id, request=request, entity_type="user", entity_id=target.id,
                 project_id=p.id, institution_id=p.institution_id, details={"role": body.role.value})
    db.commit()
    return ProjectMemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_project_member(project_id: uuid.UUID, user_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.MANAGE_MEMBERS)
    if user_id == p.owner_id:
        raise ApiError(400, "owner_role_fixed", "The project owner cannot be removed")
    m = db.scalar(select(ProjectMembership).where(ProjectMembership.project_id == p.id, ProjectMembership.user_id == user_id))
    if m is None:
        raise not_found("Membership")
    db.delete(m)
    audit.record(db, "project.member_removed", actor_id=user.id, request=request, entity_type="user", entity_id=user_id,
                 project_id=p.id, institution_id=p.institution_id)
    db.commit()
    return Response(status_code=204)


# ---------- collections ----------
@router.get("/{project_id}/collections", response_model=list[CollectionOut])
def list_collections(project_id: uuid.UUID, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.VIEW)
    return db.scalars(select(Collection).where(Collection.project_id == p.id).order_by(Collection.title)).all()


@router.post("/{project_id}/collections", response_model=CollectionOut, status_code=201)
def create_collection(project_id: uuid.UUID, body: CollectionIn, request: Request, user: CurrentUser, db: DB):
    p, _ = load_project(db, user, project_id, P.MANAGE_COLLECTIONS)
    c = Collection(project_id=p.id, **body.model_dump())
    db.add(c)
    db.flush()
    audit.record(db, "collection.create", actor_id=user.id, request=request, entity_type="collection", entity_id=c.id,
                 project_id=p.id, institution_id=p.institution_id, details={"title": c.title})
    db.commit()
    return c


def _collection(db, user, project_id, collection_id, perm):
    p, _ = load_project(db, user, project_id, perm)
    c = db.get(Collection, collection_id)
    if c is None or c.project_id != p.id:
        raise not_found("Collection")
    return p, c


@router.get("/{project_id}/collections/{collection_id}", response_model=CollectionOut)
def get_collection(project_id: uuid.UUID, collection_id: uuid.UUID, user: CurrentUser, db: DB):
    return _collection(db, user, project_id, collection_id, P.VIEW)[1]


@router.patch("/{project_id}/collections/{collection_id}", response_model=CollectionOut)
def update_collection(project_id: uuid.UUID, collection_id: uuid.UUID, body: CollectionUpdateIn, request: Request,
                      user: CurrentUser, db: DB):
    p, c = _collection(db, user, project_id, collection_id, P.MANAGE_COLLECTIONS)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(c, k, v)
    audit.record(db, "collection.update", actor_id=user.id, request=request, entity_type="collection", entity_id=c.id,
                 project_id=p.id, institution_id=p.institution_id, details={"fields": sorted(changes)})
    db.commit()
    return c


@router.delete("/{project_id}/collections/{collection_id}", status_code=204)
def delete_collection(project_id: uuid.UUID, collection_id: uuid.UUID, request: Request, user: CurrentUser, db: DB):
    p, c = _collection(db, user, project_id, collection_id, P.MANAGE_COLLECTIONS)
    audit.record(db, "collection.delete", actor_id=user.id, request=request, entity_type="collection", entity_id=c.id,
                 project_id=p.id, institution_id=p.institution_id, details={"title": c.title})
    db.delete(c)
    db.commit()
    return Response(status_code=204)
