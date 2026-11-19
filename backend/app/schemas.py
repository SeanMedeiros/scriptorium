"""Pydantic request/response schemas (API contract)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import InstitutionRole, ProjectRole

LOCALES = Literal["en", "pt", "fr"]
_ORCID = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
_LANG = re.compile(r"^[a-z]{3}$")


DUBLIN_CORE = frozenset({
    "title", "creator", "subject", "description", "publisher", "contributor", "date", "type",
    "format", "identifier", "source", "language", "relation", "coverage", "rights",
})


def _check_langs(v: list[str]) -> list[str]:
    for code in v:
        if not _LANG.match(code):
            raise ValueError(f"'{code}' is not an ISO 639-3 code (e.g. por, lat, fro)")
    return sorted(set(v))


def _check_dc(v: dict) -> dict:
    bad = [k for k in v if k not in DUBLIN_CORE]
    if bad:
        raise ValueError(f"unknown Dublin Core elements: {', '.join(bad)}")
    return v


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=256)
    preferred_locale: LOCALES = "en"


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


# ---------- users ----------
class InstitutionMembershipOut(ORM):
    institution_id: uuid.UUID
    institution_name: str
    role: InstitutionRole


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    orcid: str | None
    preferred_locale: str
    is_platform_admin: bool
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class MeOut(UserOut):
    institutions: list[InstitutionMembershipOut] = []


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    orcid: str | None = None
    preferred_locale: LOCALES | None = None

    @field_validator("orcid")
    @classmethod
    def _orcid(cls, v: str | None) -> str | None:
        if v and not _ORCID.match(v):
            raise ValueError("ORCID must look like 0000-0002-1825-0097")
        return v or None


class AdminUserUpdateIn(BaseModel):
    is_active: bool | None = None
    is_platform_admin: bool | None = None


class SessionOut(ORM):
    id: uuid.UUID
    family_id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool = False

    @field_validator("ip_address", mode="before")
    @classmethod
    def _ip(cls, v):
        return str(v) if v is not None else None


# ---------- institutions ----------
class InstitutionIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,118}$")
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    ror_id: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=500)


class InstitutionOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    country: str | None
    ror_id: str | None
    website: str | None
    created_at: datetime


class InstitutionMemberIn(BaseModel):
    email: EmailStr
    role: InstitutionRole


class InstitutionMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: InstitutionRole


# ---------- projects ----------
class ProjectIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    institution_id: uuid.UUID | None = None
    languages: list[str] = Field(default_factory=list, max_length=30)
    period_start: int | None = Field(default=None, ge=-3000, le=2100)
    period_end: int | None = Field(default=None, ge=-3000, le=2100)
    funding: str | None = Field(default=None, max_length=2000)

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        return _check_langs(v)


class ProjectUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    languages: list[str] | None = None
    period_start: int | None = Field(default=None, ge=-3000, le=2100)
    period_end: int | None = Field(default=None, ge=-3000, le=2100)
    funding: str | None = None
    is_archived: bool | None = None

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str] | None) -> list[str] | None:
        return _check_langs(v) if v is not None else None


class ProjectOut(ORM):
    id: uuid.UUID
    title: str
    slug: str
    description: str | None
    institution_id: uuid.UUID | None
    institution_name: str | None = None
    owner_id: uuid.UUID
    owner_name: str | None = None
    languages: list[str]
    period_start: int | None
    period_end: int | None
    funding: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    collection_count: int = 0
    member_count: int = 0
    permissions: list[str] = []


class ProjectMemberIn(BaseModel):
    email: EmailStr
    role: ProjectRole


class ProjectMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: ProjectRole | Literal["owner"]


# ---------- collections ----------
class CollectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    archival_reference: str | None = Field(default=None, max_length=300)
    holding_institution: str | None = Field(default=None, max_length=300)
    dc_metadata: dict[str, str | list[str]] = Field(default_factory=dict)

    @field_validator("dc_metadata")
    @classmethod
    def _dc(cls, v: dict) -> dict:
        return _check_dc(v)


class CollectionUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    archival_reference: str | None = None
    holding_institution: str | None = None
    dc_metadata: dict[str, str | list[str]] | None = None

    @field_validator("dc_metadata")
    @classmethod
    def _dc(cls, v: dict | None) -> dict | None:
        return _check_dc(v) if v is not None else None


class CollectionOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    archival_reference: str | None
    holding_institution: str | None
    dc_metadata: dict
    created_at: datetime
    updated_at: datetime


# ---------- audit / dashboard ----------
class AuditLogOut(ORM):
    id: int
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    entity_type: str | None
    entity_id: str | None
    institution_id: uuid.UUID | None
    project_id: uuid.UUID | None
    ip_address: str | None
    details: dict

    @field_validator("ip_address", mode="before")
    @classmethod
    def _ip(cls, v):
        return str(v) if v is not None else None


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class AuditPage(Page):
    items: list[AuditLogOut]


class DashboardOut(BaseModel):
    """Every figure is a COUNT over database rows visible to the user.
    Metrics belonging to modules that are not yet installed are reported as null
    together with the module name in `unavailable`."""

    projects: int
    collections: int
    researchers: int
    institutions: int
    documents: int = 0
    pages: int = 0
    processed_pages: int = 0
    recognised_lines: int = 0
    jobs: dict[str, int] = {}
    validated_models: int = 0
    storage_bytes: int = 0
    line_status: dict[str, int] = {}
    quality: dict = {}
    recent_activity: list[AuditLogOut]
    unavailable: dict[str, str]
