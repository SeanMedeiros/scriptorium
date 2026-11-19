"""Institutional and project reporting (master prompt §5 phase "institutional reporting", §16).

Every figure is computed from database rows at request time. Error rates appear only where
ground-truth evaluation results exist, always with their methodology.
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import func, select, text

from app.api.deps import DB, CurrentUser, load_project
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.core.permissions import institution_role
from app.evaluation import metrics
from app.evaluation import service as ev
from app.models import (
    Collection,
    Document,
    EvaluationDataset,
    Export,
    GroundTruthPage,
    Institution,
    InstitutionRole,
    LineRecognition,
    ModelVersion,
    Page,
    Project,
    Recognition,
    RecognitionJob,
    Transcription,
    TranscriptionRevision,
    User,
)

router = APIRouter(tags=["reports"])


def project_report(db, project: Project) -> dict:
    col_ids = select(Collection.id).where(Collection.project_id == project.id)
    doc_ids = select(Document.id).where(Document.collection_id.in_(col_ids))
    page_ids = list(db.scalars(select(Page.id).where(Page.document_id.in_(doc_ids))))
    n = lambda q: db.scalar(q) or 0  # noqa: E731
    counts = {
        "collections": n(select(func.count()).select_from(col_ids.subquery())),
        "documents": n(select(func.count()).select_from(doc_ids.subquery())),
        "pages": len(page_ids),
        "processed_pages": n(select(func.count(func.distinct(Recognition.page_id))).where(Recognition.page_id.in_(page_ids))),
        "recognitions": n(select(func.count(Recognition.id)).where(Recognition.page_id.in_(page_ids))),
        "recognised_lines": n(select(func.count(LineRecognition.id)).join(Recognition).where(Recognition.page_id.in_(page_ids))),
        "transcriptions": n(select(func.count(Transcription.id)).where(Transcription.page_id.in_(page_ids))),
        "revisions": n(select(func.count(TranscriptionRevision.id)).join(Transcription)
                       .where(Transcription.page_id.in_(page_ids), TranscriptionRevision.kind != "initial")),
        "ground_truth_pages": n(select(func.count(GroundTruthPage.id)).join(EvaluationDataset)
                                .where(EvaluationDataset.project_id == project.id)),
        "exports": n(select(func.count(Export.id)).where(Export.project_id == project.id, Export.status == "completed")),
    }
    jobs = {s.value: c for s, c in db.execute(select(RecognitionJob.status, func.count())
                                              .where(RecognitionJob.project_id == project.id).group_by(RecognitionJob.status))}
    # Current state of every line of every transcription (latest version per line).
    cur = db.execute(text("""
        SELECT cur.status, count(*), sum(cur.uncertain::int), sum(cur.illegible::int)
        FROM (SELECT DISTINCT ON (lv.transcription_id, lv.line_id) lv.status, lv.has_uncertain AS uncertain,
                     lv.has_illegible AS illegible
              FROM line_versions lv JOIN transcription_revisions r ON r.id = lv.revision_id
              JOIN transcriptions t ON t.id = lv.transcription_id
              WHERE t.page_id = ANY(:pages)
              ORDER BY lv.transcription_id, lv.line_id, r.number DESC) cur
        GROUP BY cur.status"""), {"pages": page_ids}).all()
    lines = {"unreviewed": 0, "corrected": 0, "reviewed": 0, "uncertain": 0, "illegible": 0}
    for st, c, u, il in cur:
        lines[st] = c
        lines["uncertain"] += u or 0
        lines["illegible"] += il or 0
    total = lines["unreviewed"] + lines["corrected"] + lines["reviewed"]
    lines["total"] = total
    lines["reviewed_share"] = lines["reviewed"] / total if total else None
    models = [{"model": f"{mv.model.name} · {mv.version}", "model_version_id": str(mv.id), "doi": mv.doi,
               "recognitions": c, "pages": p}
              for mv, c, p in db.execute(select(ModelVersion, func.count(Recognition.id), func.count(func.distinct(Recognition.page_id)))
                                         .join(Recognition, Recognition.model_version_id == ModelVersion.id)
                                         .where(Recognition.page_id.in_(page_ids)).group_by(ModelVersion.id))]
    contributors = [{"name": name, "email": email, "revisions": revs, "lines_changed": changed,
                     "last_activity": last.isoformat() if last else None}
                    for name, email, revs, changed, last in db.execute(
                        select(User.full_name, User.email, func.count(TranscriptionRevision.id),
                               func.coalesce(func.sum(TranscriptionRevision.change_count), 0),
                               func.max(TranscriptionRevision.created_at))
                        .join(TranscriptionRevision, TranscriptionRevision.author_id == User.id)
                        .join(Transcription, Transcription.id == TranscriptionRevision.transcription_id)
                        .where(Transcription.page_id.in_(page_ids), TranscriptionRevision.kind != "initial")
                        .group_by(User.id).order_by(func.count(TranscriptionRevision.id).desc()))]
    rows = ev.results_table(db, project.id)
    evaluation = {"methodology_version": metrics.METHODOLOGY_VERSION, "results": len(rows),
                  "by_model": ev.group(rows, "model") if rows else [],
                  "by_period": ev.group(rows, "period") if rows else [],
                  "by_language": ev.group(rows, "language") if rows else []}
    return {"project": {"id": str(project.id), "title": project.title}, "counts": counts, "jobs": jobs,
            "lines": lines, "models": models, "contributors": contributors, "evaluation": evaluation}


def to_csv(rep: dict) -> bytes:
    b = io.StringIO()
    w = csv.writer(b)
    w.writerow(["section", "key", "value_1", "value_2", "value_3", "value_4"])
    for k, v in rep["counts"].items():
        w.writerow(["counts", k, v])
    for k, v in rep["jobs"].items():
        w.writerow(["jobs", k, v])
    for k, v in rep["lines"].items():
        w.writerow(["lines", k, v])
    for m in rep["models"]:
        w.writerow(["models", m["model"], m["recognitions"], m["pages"], m["doi"]])
    for c in rep["contributors"]:
        w.writerow(["contributors", c["name"], c["revisions"], c["lines_changed"], c["last_activity"]])
    for g in rep["evaluation"]["by_model"]:
        w.writerow(["evaluation_by_model", g["key"], g["cer"], g["wer"], g["ref_chars"], g["pages"]])
    return b.getvalue().encode("utf-8")


@router.get("/projects/{project_id}/report")
def get_project_report(project_id: uuid.UUID, user: CurrentUser, db: DB, format: str = "json"):
    project, _ = load_project(db, user, project_id, P.VIEW)
    rep = project_report(db, project)
    if format == "csv":
        return Response(to_csv(rep), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="report-{project.slug}.csv"'})
    return rep


@router.get("/institutions/{institution_id}/report")
def get_institution_report(institution_id: uuid.UUID, user: CurrentUser, db: DB):
    inst = db.get(Institution, institution_id)
    if inst is None:
        raise not_found("institution")
    if not user.is_platform_admin and institution_role(db, user, inst.id) != InstitutionRole.INSTITUTION_ADMIN:
        raise ApiError(403, "forbidden", "Institution reports are available to institution administrators")
    projects = list(db.scalars(select(Project).where(Project.institution_id == inst.id).order_by(Project.title)))
    reps = [project_report(db, p) for p in projects]
    totals = {}
    for r in reps:
        for k, v in r["counts"].items():
            totals[k] = totals.get(k, 0) + v
    lines = {k: sum(r["lines"][k] for r in reps) for k in ("unreviewed", "corrected", "reviewed", "uncertain", "illegible", "total")}
    lines["reviewed_share"] = lines["reviewed"] / lines["total"] if lines["total"] else None
    return {"institution": {"id": str(inst.id), "name": inst.name}, "totals": totals, "lines": lines,
            "projects": [{"id": r["project"]["id"], "title": r["project"]["title"], "counts": r["counts"],
                          "lines": r["lines"], "evaluation_by_model": r["evaluation"]["by_model"]} for r in reps]}
