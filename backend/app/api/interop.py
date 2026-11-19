"""Annotation management, advanced analytics and extended interoperability (Phase 6).

* Annotations: project-wide listing/filtering and export as a W3C Web Annotation collection.
* Analytics: character confusions, error by line length and an empirical reliability diagram
  (model confidence vs measured accuracy) from stored, line-aligned evaluation results.
* IIIF Presentation API 3.0 manifest per document, with the transcription lines as
  `supplementing` annotations targeting canvas regions; validated against the JSON Schema of the
  official IIIF presentation validator (vendored).
"""
from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, load_project
from app.config import get_settings
from app.core.errors import ApiError, not_found
from app.core.permissions import ProjectPermission as P
from app.evaluation import metrics
from app.exports import context as ctx
from app.models import (
    Annotation,
    Collection,
    Document,
    EvaluationDataset,
    EvaluationResult,
    GroundTruthPage,
    ImageAsset,
    LineRecognition,
    ModelVersion,
    Page,
    Recognition,
    Segmentation,
    TextLine,
    Transcription,
    User,
)
from app.services.asset_urls import signed_url

router = APIRouter(tags=["interoperability"])
W3C_MOTIVATION = {"comment": "commenting", "query": "questioning", "palaeography": "describing"}


# ------------------------------------------------------------------------------- annotations
def _annotation_rows(db, project_id: uuid.UUID, kind: str | None, status: str | None, q: str | None):
    stmt = (select(Annotation, Transcription, Page, Document, User)
            .join(Transcription, Transcription.id == Annotation.transcription_id)
            .join(Page, Page.id == Transcription.page_id).join(Document, Document.id == Page.document_id)
            .join(Collection, Collection.id == Document.collection_id)
            .outerjoin(User, User.id == Annotation.author_id)
            .where(Collection.project_id == project_id).order_by(Annotation.created_at.desc()))
    if kind:
        stmt = stmt.where(Annotation.kind == kind)
    if status == "open":
        stmt = stmt.where(Annotation.resolved_at.is_(None))
    elif status == "resolved":
        stmt = stmt.where(Annotation.resolved_at.is_not(None))
    if q:
        stmt = stmt.where(Annotation.body.ilike(f"%{q}%"))
    return db.execute(stmt.limit(2000)).all()


@router.get("/projects/{project_id}/annotations")
def list_annotations(project_id: uuid.UUID, user: CurrentUser, db: DB, kind: str | None = None,
                     status: str | None = None, q: str | None = None):
    project, _ = load_project(db, user, project_id, P.VIEW)
    out = []
    lines = {}
    for a, t, page, doc, author in _annotation_rows(db, project.id, kind, status, q):
        if a.line_id and a.line_id not in lines:
            ln = db.get(TextLine, a.line_id)
            lines[a.line_id] = ln.order_index + 1 if ln else None
        out.append({"id": str(a.id), "kind": a.kind, "body": a.body, "author": author.full_name if author else None,
                    "created_at": a.created_at.isoformat(), "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                    "transcription_id": str(t.id), "page_id": str(page.id), "page_sequence": page.sequence,
                    "document_id": str(doc.id), "document_title": doc.title,
                    "line_number": lines.get(a.line_id) if a.line_id else None})
    counts = Counter(("resolved" if x["resolved_at"] else "open") for x in out)
    return {"items": out, "counts": {"open": counts.get("open", 0), "resolved": counts.get("resolved", 0),
                                     "by_kind": dict(Counter(x["kind"] for x in out))}}


@router.get("/projects/{project_id}/annotations/w3c")
def export_annotations(project_id: uuid.UUID, user: CurrentUser, db: DB):
    """W3C Web Annotation Data Model collection (JSON-LD). Targets: the page image region of the
    annotated line (media-fragment `xywh`) on the platform's IIIF canvas of that page."""
    project, _ = load_project(db, user, project_id, P.EXPORT)
    base = get_settings().public_base_url.rstrip("/")
    items = []
    for a, t, page, doc, author in _annotation_rows(db, project.id, None, None, None):
        canvas = f"{base}/api/v1/documents/{doc.id}/iiif/canvas/p{page.sequence}"
        target: dict | str = canvas
        if a.line_id and (ln := db.get(TextLine, a.line_id)):
            x0, y0, x1, y1 = ln.bbox
            target = {"type": "SpecificResource", "source": canvas,
                      "selector": {"type": "FragmentSelector", "conformsTo": "http://www.w3.org/TR/media-frags/",
                                   "value": f"xywh={int(x0)},{int(y0)},{int(x1 - x0)},{int(y1 - y0)}"}}
        item = {"id": f"urn:uuid:{a.id}", "type": "Annotation", "motivation": W3C_MOTIVATION.get(a.kind, "commenting"),
                "created": a.created_at.isoformat(), "body": {"type": "TextualBody", "value": a.body, "format": "text/plain"},
                "target": target}
        if author:
            item["creator"] = {"type": "Person", "name": author.full_name}
        if a.resolved_at:
            item["modified"] = a.resolved_at.isoformat()
            item["status"] = "resolved"  # extension property (not part of the W3C vocabulary)
        items.append(item)
    coll = {"@context": "http://www.w3.org/ns/anno.jsonld", "id": f"{base}/api/v1/projects/{project.id}/annotations/w3c",
            "type": "AnnotationCollection", "label": f"Annotations — {project.title}", "total": len(items),
            "first": {"type": "AnnotationPage", "items": items}}
    return JSONResponse(coll, media_type="application/ld+json")


# ------------------------------------------------------------------------------- analytics
def _bins_conf():
    return [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.0001)]


@router.get("/projects/{project_id}/analytics")
def analytics(project_id: uuid.UUID, user: CurrentUser, db: DB, model_version_id: uuid.UUID | None = None,
              dataset_id: uuid.UUID | None = None, top: int = 25):
    """Error analysis of stored line-aligned evaluation results (latest result per page and model)."""
    from rapidfuzz.distance import Levenshtein

    project, _ = load_project(db, user, project_id, P.VIEW)
    q = (select(EvaluationResult, GroundTruthPage, ModelVersion)
         .join(GroundTruthPage, GroundTruthPage.id == EvaluationResult.gt_page_id)
         .join(EvaluationDataset, EvaluationDataset.id == GroundTruthPage.dataset_id)
         .join(ModelVersion, ModelVersion.id == EvaluationResult.model_version_id)
         .where(EvaluationDataset.project_id == project.id, EvaluationResult.alignment == "line")
         .order_by(EvaluationResult.created_at.desc()))
    if model_version_id:
        q = q.where(EvaluationResult.model_version_id == model_version_id)
    if dataset_id:
        q = q.where(EvaluationDataset.id == dataset_id)
    seen, results = set(), []
    for r, gt, mv in db.execute(q).all():
        if (gt.id, mv.id) in seen:
            continue
        seen.add((gt.id, mv.id))
        results.append((r, gt, mv))
    subs, dels, ins = Counter(), Counter(), Counter()
    by_len = defaultdict(lambda: [0, 0, 0])  # chars, errors, lines
    conf_bins = {b: [0, 0, 0, 0.0] for b in _bins_conf()}  # chars, errors, lines, sum_conf
    models, methods = set(), set()
    for r, gt, mv in results:
        models.add(f"{mv.model.name} · {mv.version}")
        methods.add(r.methodology.get("version"))
        rec = db.get(Recognition, r.recognition_id)
        conf = {}
        for lr, ln in db.execute(select(LineRecognition, TextLine).join(TextLine, TextLine.id == LineRecognition.line_id)
                                 .where(LineRecognition.recognition_id == rec.id)).all():
            conf[(ln.tags or {}).get("source_line_id") or str(ln.id)] = lr.mean_confidence
        for ln in r.details.get("lines", []):
            ref, hyp = ln["ref"], ln["hyp"] or ""
            for op, i, j in Levenshtein.editops(ref, hyp):
                if op == "replace":
                    subs[(ref[i], hyp[j])] += 1
                elif op == "delete":
                    dels[ref[i]] += 1
                else:
                    ins[hyp[j]] += 1
            n = ln["ref_chars"]
            bucket = "0–19" if n < 20 else "20–39" if n < 40 else "40–59" if n < 60 else "60+"
            b = by_len[bucket]
            b[0] += n
            b[1] += ln["char_errors"]
            b[2] += 1
            c = conf.get(ln["line_id"])
            if c is not None and n:
                for lo, hi in conf_bins:
                    if lo <= c < hi:
                        cb = conf_bins[(lo, hi)]
                        cb[0] += n
                        cb[1] += ln["char_errors"]
                        cb[2] += 1
                        cb[3] += c
                        break
    calib, ece_num, ece_den = [], 0.0, 0
    for (lo, hi), (chars, errs, lines, sconf) in conf_bins.items():
        if not lines:
            continue
        acc = metrics.accuracy(errs / chars) if chars else None
        mean_conf = sconf / lines
        calib.append({"bin": f"{lo:.2f}–{min(hi, 1):.2f}", "lines": lines, "chars": chars, "mean_confidence": mean_conf,
                      "measured_char_accuracy": acc})
        if acc is not None:
            ece_num += lines * abs(mean_conf - acc)
            ece_den += lines
    fmt = lambda c: c if c.strip() else repr(c)  # noqa: E731
    return {"results": len(results), "models": sorted(models), "methodology_versions": sorted(x for x in methods if x),
            "confusions": [{"ref": fmt(a), "hyp": fmt(b), "count": n} for (a, b), n in subs.most_common(top)],
            "deletions": [{"char": fmt(a), "count": n} for a, n in dels.most_common(top)],
            "insertions": [{"char": fmt(a), "count": n} for a, n in ins.most_common(top)],
            "by_line_length": [{"bucket": k, "lines": v[2], "chars": v[0], "cer": v[1] / v[0] if v[0] else None}
                               for k, v in sorted(by_len.items(), key=lambda kv: ["0–19", "20–39", "40–59", "60+"].index(kv[0]))],
            "calibration": calib,
            "expected_calibration_error": ece_num / ece_den if ece_den else None,
            "note": "Model confidence is the mean per-character score reported by the network. The reliability "
                    "diagram compares it with character accuracy measured against ground truth; the gap shows that "
                    "confidences are not calibrated probabilities."}


# ------------------------------------------------------------------------------------- IIIF
def _lang_map(text: str, lang: str | None = None) -> dict:
    return {lang or "none": [text]}


@router.get("/documents/{document_id}/iiif/manifest.json")
def iiif_manifest(document_id: uuid.UUID, user: CurrentUser, db: DB, layer: str = "corrected"):
    """IIIF Presentation 3.0 manifest. Image URLs are signed and expire (default 1 h): the manifest is
    meant for authenticated use and for exchange of structure and text, not as a public image service."""
    from app.exports.validate import validate_iiif

    doc = db.get(Document, document_id)
    if doc is None:
        raise not_found("document")
    col = db.get(Collection, doc.collection_id)
    project, _ = load_project(db, user, col.project_id, P.EXPORT)
    s = get_settings()
    base = s.public_base_url.rstrip("/")
    mid = f"{base}/api/v1/documents/{doc.id}/iiif"
    lang = (doc.languages or [None])[0]
    canvases, skipped = [], []
    for page in db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.sequence)):
        tx = ctx.pick_transcription(db, page) if layer != "htr" else None
        rec = None if tx else ctx.pick_recognition(db, page)
        cid = f"{mid}/canvas/p{page.sequence}"
        pc = None
        if tx or rec:
            try:
                pc = ctx.page_context(db, page, layer if tx else "htr", transcription=tx, recognition=rec)
            except ctx.ExportError as e:
                skipped.append({"page": page.sequence, "reason": e.message})
        seg_asset = None
        if pc:
            seg_asset = db.get(ImageAsset, db.get(Segmentation, uuid.UUID(pc.segmentation["id"])).input_asset_id)
        asset = seg_asset or db.get(ImageAsset, page.image_asset_id)
        img_url = f"{base}{signed_url(asset.id)}"
        canvas = {"id": cid, "type": "Canvas", "label": _lang_map(page.label or f"p. {page.sequence}"),
                  "height": asset.height, "width": asset.width,
                  "items": [{"id": f"{cid}/painting", "type": "AnnotationPage", "items": [{
                      "id": f"{cid}/painting/image", "type": "Annotation", "motivation": "painting", "target": cid,
                      "body": {"id": img_url, "type": "Image", "format": asset.mime_type, "height": asset.height,
                               "width": asset.width}}]}]}
        if pc and pc.lines:
            canvas["annotations"] = [{
                "id": f"{cid}/transcription", "type": "AnnotationPage",
                "label": _lang_map(f"{'Uncorrected HTR output' if pc.text_source == 'htr' else pc.text_source}, layer {pc.layer}"),
                "items": [{"id": f"{cid}/line/{ln.n}", "type": "Annotation", "motivation": "supplementing",
                           "body": {"type": "TextualBody", "value": ln.text, "format": "text/plain",
                                    **({"language": lang} if lang else {})},
                           "target": f"{cid}#xywh={int(ln.bbox[0])},{int(ln.bbox[1])},{int(ln.bbox[2] - ln.bbox[0])},{int(ln.bbox[3] - ln.bbox[1])}"}
                          for ln in pc.lines if ln.text]}]
        canvases.append(canvas)
    md = ctx.document_metadata(doc, col)
    metadata = [{"label": _lang_map(k, "en"), "value": _lang_map(str(v))} for k, v in (
        ("Archival reference", md.get("archival_reference")), ("Date", md.get("date")),
        ("Languages", ", ".join(md.get("languages") or [])), ("Collection", md.get("collection")),
        ("Provenance", md.get("provenance"))) if v]
    manifest = {"@context": "http://iiif.io/api/presentation/3/context.json", "id": f"{mid}/manifest.json",
                "type": "Manifest", "label": _lang_map(doc.title), "metadata": metadata,
                "summary": _lang_map(f"Exported from SCRIPTORIUM (project “{project.title}”); transcription layer "
                                     f"'{layer}'. Image links expire.", "en"),
                "items": canvases}
    v = validate_iiif(manifest)
    if not v["valid"]:  # never serve an invalid manifest silently
        raise ApiError(500, "iiif_invalid", "The generated IIIF manifest does not validate", details=v)
    return JSONResponse(manifest, media_type='application/ld+json;profile="http://iiif.io/api/presentation/3/context.json"',
                        headers={"X-Scriptorium-Validation": json.dumps({k: v[k] for k in ("valid", "schema", "method", "errors")}),
                                 "Content-Disposition": f'attachment; filename="manifest-{str(doc.id)[:8]}.json"',
                                 "Access-Control-Expose-Headers": "X-Scriptorium-Validation, Content-Disposition"})

