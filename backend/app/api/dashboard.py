"""Dashboard and audit log. Every number is a COUNT over real rows."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select, text, union

from app.api.deps import DB, CurrentUser
from app.core.permissions import visible_projects_query
from app.models import (
    AuditLog,
    Collection,
    Document,
    ImageAsset,
    Institution,
    InstitutionMembership,
    InstitutionRole,
    JobStatus,
    LineRecognition,
    ModelStatus,
    ModelVersion,
    Page,
    Project,
    ProjectMembership,
    Recognition,
    RecognitionJob,
    Segmentation,
    User,
)
from app.schemas import AuditLogOut, AuditPage, DashboardOut

router = APIRouter(tags=["dashboard"])

# Modules whose metrics will appear once their phase is delivered. Reported, never faked.
_PENDING: dict[str, str] = {}


def _audit_scope(db, user: User):
    stmt = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id)
    if user.is_platform_admin:
        return stmt
    visible = visible_projects_query(user).with_only_columns(Project.id)
    admin_inst = select(InstitutionMembership.institution_id).where(
        InstitutionMembership.user_id == user.id, InstitutionMembership.role == InstitutionRole.INSTITUTION_ADMIN)
    return stmt.where(or_(AuditLog.actor_id == user.id, AuditLog.project_id.in_(visible),
                          AuditLog.institution_id.in_(admin_inst)))


def _rows_out(rows) -> list[AuditLogOut]:
    out = []
    for log, email in rows:
        o = AuditLogOut.model_validate(log)
        o.actor_email = email
        out.append(o)
    return out


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user: CurrentUser, db: DB):
    visible_ids = visible_projects_query(user).with_only_columns(Project.id).subquery()
    projects = db.scalar(select(func.count()).select_from(visible_ids))
    collections = db.scalar(select(func.count(Collection.id)).where(Collection.project_id.in_(select(visible_ids))))
    owners = select(Project.owner_id.label("uid")).where(Project.id.in_(select(visible_ids)))
    members = select(ProjectMembership.user_id.label("uid")).where(ProjectMembership.project_id.in_(select(visible_ids)))
    researchers = db.scalar(select(func.count(func.distinct(owners.union(members).subquery().c.uid))))
    if user.is_platform_admin:
        institutions = db.scalar(select(func.count(Institution.id)))
    else:
        institutions = db.scalar(select(func.count(InstitutionMembership.id)).where(InstitutionMembership.user_id == user.id))
    recent = db.execute(_audit_scope(db, user).order_by(AuditLog.id.desc()).limit(10)).all()
    coll_ids = select(Collection.id).where(Collection.project_id.in_(select(visible_ids)))
    doc_ids = select(Document.id).where(Document.collection_id.in_(coll_ids))
    page_ids = select(Page.id).where(Page.document_id.in_(doc_ids))
    documents = db.scalar(select(func.count()).select_from(doc_ids.subquery())) or 0
    pages = db.scalar(select(func.count()).select_from(page_ids.subquery())) or 0
    processed_pages = db.scalar(select(func.count(func.distinct(Recognition.page_id))).where(Recognition.page_id.in_(page_ids))) or 0
    recognised_lines = db.scalar(select(func.count(LineRecognition.id)).join(Recognition)
                                 .where(Recognition.page_id.in_(page_ids))) or 0
    jobs = dict(db.execute(select(RecognitionJob.status, func.count()).where(RecognitionJob.project_id.in_(select(visible_ids)))
                           .group_by(RecognitionJob.status)).all())
    models = db.scalar(select(func.count(ModelVersion.id)).where(ModelVersion.status == ModelStatus.VALIDATED)) or 0
    # Storage: distinct stored objects referenced by visible documents (originals + page images + derivatives).
    orig = select(Document.source_asset_id.label("aid")).where(Document.id.in_(doc_ids))
    pimg = select(Page.image_asset_id.label("aid")).where(Page.id.in_(page_ids))
    seg_in = select(Segmentation.input_asset_id.label("aid")).where(Segmentation.page_id.in_(page_ids))
    used = union(orig, pimg, seg_in).subquery()
    storage_bytes = db.scalar(select(func.coalesce(func.sum(ImageAsset.size_bytes), 0))
                              .where(ImageAsset.id.in_(select(used.c.aid)))) or 0
    project_list = list(db.scalars(select(visible_ids.c.id)))  # small; page ids stay in SQL (Phase 7)
    # Current status of every transcribed line (latest version per line), visible pages only.
    status_rows = db.execute(text("""
        SELECT status, count(*) FROM (
          SELECT DISTINCT ON (lv.transcription_id, lv.line_id) lv.status
          FROM line_versions lv JOIN transcription_revisions r ON r.id = lv.revision_id
          JOIN transcriptions t ON t.id = lv.transcription_id
          JOIN pages p ON p.id = t.page_id JOIN documents d ON d.id = p.document_id
          JOIN collections c ON c.id = d.collection_id
          WHERE c.project_id = ANY(:projects)
          ORDER BY lv.transcription_id, lv.line_id, r.number DESC) cur
        GROUP BY status"""), {"projects": project_list}).all()
    line_status = {"unreviewed": 0, "corrected": 0, "reviewed": 0} | {s: n for s, n in status_rows}
    # Quality: CER/WER only from stored ground-truth evaluations (micro-averaged per model).
    from app.evaluation import metrics
    from app.evaluation import service as ev

    rows = [r for pid in project_list for r in ev.results_table(db, pid)]
    quality = {"ground_truth_pages": db.scalar(text(
        "SELECT count(*) FROM ground_truth_pages g JOIN evaluation_datasets d ON d.id = g.dataset_id "
        "WHERE d.project_id = ANY(:p) AND g.id NOT IN (SELECT gt_page_id FROM ground_truth_withdrawals)"),
        {"p": project_list}) or 0,
        "evaluation_results": len(rows), "methodology_version": metrics.METHODOLOGY_VERSION,
        "by_model": ev.group(rows, "model") if rows else []}
    return DashboardOut(line_status=line_status, quality=quality, projects=projects or 0, collections=collections or 0, researchers=researchers or 0,
                        institutions=institutions or 0, documents=documents, pages=pages, processed_pages=processed_pages,
                        recognised_lines=recognised_lines, jobs={k.value: jobs.get(k, 0) for k in JobStatus},
                        validated_models=models, storage_bytes=int(storage_bytes),
                        recent_activity=_rows_out(recent), unavailable=_PENDING)


@router.get("/audit-logs", response_model=AuditPage)
def audit_logs(user: CurrentUser, db: DB, action: str | None = None, project_id: uuid.UUID | None = None,
               limit: int = Query(50, le=500), offset: int = 0):
    stmt = _audit_scope(db, user)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if project_id:
        stmt = stmt.where(AuditLog.project_id == project_id)
    total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(AuditLog.id).subquery()))
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return AuditPage(total=total or 0, limit=limit, offset=offset, items=_rows_out(rows))
