"""Central authorization rules.

Scopes: platform (is_platform_admin) > institution (institution_admin) > project role.
All listing queries go through `visible_projects_query` so that institution-level
isolation is enforced in one place.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import InstitutionMembership, InstitutionRole, Project, ProjectMembership, ProjectRole, User


class ProjectPermission(str, enum.Enum):
    VIEW = "view"
    EDIT_METADATA = "edit_metadata"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_COLLECTIONS = "manage_collections"
    UPLOAD = "upload"
    RUN_RECOGNITION = "run_recognition"
    TRANSCRIBE = "transcribe"
    REVIEW = "review"
    EXPORT = "export"
    DELETE = "delete"
    TRAIN_MODELS = "train_models"  # fine-tuning, institutional model upload (Phase 6)


P = ProjectPermission
ROLE_PERMISSIONS: dict[ProjectRole, frozenset[ProjectPermission]] = {
    ProjectRole.PRINCIPAL_INVESTIGATOR: frozenset(P),
    ProjectRole.RESEARCHER: frozenset(
        {P.VIEW, P.MANAGE_COLLECTIONS, P.UPLOAD, P.RUN_RECOGNITION, P.TRANSCRIBE, P.REVIEW, P.EXPORT, P.TRAIN_MODELS}
    ),
    ProjectRole.TRANSCRIPTION_EDITOR: frozenset({P.VIEW, P.TRANSCRIBE, P.EXPORT}),
    ProjectRole.REVIEWER: frozenset({P.VIEW, P.REVIEW, P.EXPORT}),
    ProjectRole.READ_ONLY: frozenset({P.VIEW}),
}


def institution_role(db: Session, user: User, institution_id: uuid.UUID | None) -> InstitutionRole | None:
    if institution_id is None:
        return None
    return db.scalar(
        select(InstitutionMembership.role).where(
            InstitutionMembership.user_id == user.id, InstitutionMembership.institution_id == institution_id
        )
    )


def project_permissions(db: Session, user: User, project: Project) -> frozenset[ProjectPermission]:
    if user.is_platform_admin:
        return frozenset(P)
    if project.institution_id and institution_role(db, user, project.institution_id) == InstitutionRole.INSTITUTION_ADMIN:
        return frozenset(P)
    if project.owner_id == user.id:
        return frozenset(P)
    role = db.scalar(
        select(ProjectMembership.role).where(ProjectMembership.project_id == project.id, ProjectMembership.user_id == user.id)
    )
    return ROLE_PERMISSIONS.get(role, frozenset()) if role else frozenset()


def visible_projects_query(user: User):
    """SELECT of projects the user may see. Platform admins see everything."""
    q = select(Project)
    if user.is_platform_admin:
        return q
    admin_institutions = select(InstitutionMembership.institution_id).where(
        InstitutionMembership.user_id == user.id, InstitutionMembership.role == InstitutionRole.INSTITUTION_ADMIN
    )
    member_projects = select(ProjectMembership.project_id).where(ProjectMembership.user_id == user.id)
    return q.where(
        or_(
            Project.owner_id == user.id,
            Project.id.in_(member_projects),
            Project.institution_id.in_(admin_institutions),
        )
    )
