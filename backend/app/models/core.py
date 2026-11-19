"""Phase 1 domain model: identity, institutions, projects, collections, audit."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Timestamps, UUIDPk, utcnow


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e], native_enum=True)


class InstitutionRole(str, enum.Enum):
    INSTITUTION_ADMIN = "institution_admin"
    MEMBER = "member"


class ProjectRole(str, enum.Enum):
    PRINCIPAL_INVESTIGATOR = "principal_investigator"
    RESEARCHER = "researcher"
    TRANSCRIPTION_EDITOR = "transcription_editor"
    REVIEWER = "reviewer"
    READ_ONLY = "read_only"


class Institution(UUIDPk, Timestamps, Base):
    __tablename__ = "institutions"

    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    ror_id: Mapped[str | None] = mapped_column(String(64))  # Research Organization Registry
    website: Mapped[str | None] = mapped_column(String(500))
    storage_policy: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    memberships: Mapped[list[InstitutionMembership]] = relationship(back_populates="institution", cascade="all, delete-orphan")


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320))
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    orcid: Mapped[str | None] = mapped_column(String(19))
    preferred_locale: Mapped[str] = mapped_column(String(5), default="en", server_default="en")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())

    memberships: Mapped[list[InstitutionMembership]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)


class InstitutionMembership(UUIDPk, Timestamps, Base):
    __tablename__ = "institution_memberships"

    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[InstitutionRole] = mapped_column(_enum(InstitutionRole, "institution_role"))

    institution: Mapped[Institution] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (UniqueConstraint("institution_id", "user_id", name="uq_institution_member"),)


class UserSession(UUIDPk, Base):
    """A refresh-token session. Tokens are stored as SHA-256 hashes and rotated."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_sessions.id", ondelete="SET NULL"), index=True)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))


class PasswordResetToken(UUIDPk, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"

    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("institutions.id", ondelete="RESTRICT"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")  # ISO 639-3
    period_start: Mapped[int | None]
    period_end: Mapped[int | None]
    funding: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Default transcription policy for new transcriptions (see app.editorial.policy).
    transcription_policy: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    institution: Mapped[Institution | None] = relationship()
    owner: Mapped[User] = relationship()
    memberships: Mapped[list[ProjectMembership]] = relationship(back_populates="project", cascade="all, delete-orphan")
    collections: Mapped[list[Collection]] = relationship(back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("period_start IS NULL OR period_end IS NULL OR period_start <= period_end", name="period_order"),
        Index("uq_projects_owner_slug", "owner_id", "slug", unique=True),
    )


class ProjectMembership(UUIDPk, Timestamps, Base):
    __tablename__ = "project_memberships"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[ProjectRole] = mapped_column(_enum(ProjectRole, "project_role"))

    project: Mapped[Project] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)


class Collection(UUIDPk, Timestamps, Base):
    __tablename__ = "collections"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    archival_reference: Mapped[str | None] = mapped_column(String(300))  # e.g. fonds / shelfmark
    holding_institution: Mapped[str | None] = mapped_column(String(300))
    # Dublin Core-compatible descriptive metadata (dc:* keys).
    dc_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    project: Mapped[Project] = relationship(back_populates="collections")


class AuditLog(Base):
    """Append-only audit trail. Never updated or deleted by the application."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # no FK: the trail outlives the entity
    institution_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # no FK: the trail outlives the entity
    project_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # no FK: the trail outlives the entity
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
