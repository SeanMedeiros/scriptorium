"""Collect everything an export needs for one page, with complete provenance (master prompt §13, §19).

Text layers (``layer`` option):
* ``htr``        — the recognition output exactly as produced by the model (immutable)
* ``corrected``  — researcher-corrected text, diplomatic rendering of the editorial markup
* ``expanded``   — corrected text with the editor's abbreviation expansions
* ``reading``    — expanded, deletions dropped, editorial additions included
* ``normalized`` — the editorially normalised layer entered by researchers (empty where none was given)
* ``markup``     — corrected text with SCRIPTORIUM markup v1 (TXT/JSON only; TEI maps the markup itself)

A page without a transcription can only be exported with its HTR output; every format states which
text source was used for each page (`text_source`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app import __version__
from app.editorial import markup
from app.editorial.service import current_versions
from app.models import (
    Collection,
    Document,
    EvaluationResult,
    GroundTruthPage,
    ImageAsset,
    LineRecognition,
    LineStatus,
    ModelVersion,
    Page,
    Project,
    Recognition,
    Segmentation,
    TextLine,
    TextRegion,
    Transcription,
    TranscriptionRevision,
    User,
)

LAYERS = ("htr", "corrected", "expanded", "reading", "normalized", "markup")
_RENDER = {"corrected": "diplomatic", "expanded": "expanded", "reading": "reading"}


class ExportError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass
class LineCtx:
    id: str
    n: int
    region_id: str | None
    bbox: list[int]
    baseline: list | None
    boundary: list | None
    text: str  # in the selected layer
    markup: str | None  # corrected text with markup (None for HTR-only pages)
    htr: str | None
    htr_confidence: float | None
    normalized: str | None
    note: str | None
    status: str | None
    origin_model: str | None = None


@dataclass
class RegionCtx:
    id: str
    type: str
    boundary: list | None
    line_ids: list[str] = field(default_factory=list)


@dataclass
class PageCtx:
    id: str
    sequence: int
    label: str | None
    image: dict
    text_source: str  # "transcription r<N>" | "htr"
    layer: str
    lines: list[LineCtx]
    regions: list[RegionCtx]
    segmentation: dict
    recognition: dict | None
    transcription: dict | None
    quality: dict


@dataclass
class DocCtx:
    id: str
    title: str
    metadata: dict
    project: dict
    pages: list[PageCtx]
    export: dict


def _mv_info(mv: ModelVersion | None) -> dict | None:
    if mv is None:
        return None
    from app.models import ModelLicense

    sm = mv.source_metadata or {}
    lic = None
    if mv.license_id:
        from sqlalchemy.orm import object_session

        lic = object_session(mv).get(ModelLicense, mv.license_id)
    return {"model_version_id": str(mv.id), "name": mv.model.name, "slug": mv.model.slug, "version": mv.version,
            "doi": mv.doi, "zenodo_record": mv.record_id, "concept_doi": mv.model.concept_doi,
            "file": mv.file_name, "sha256": mv.sha256, "source_checksum": mv.source_checksum,
            "license": mv.license_id, "license_url": lic.url if lic else None,
            "authors": [c.get("name") for c in (sm.get("zenodo") or {}).get("creators", []) if c.get("name")],
            "publication_date": mv.publication_date, "unicode_normalization": mv.unicode_normalization,
            "source_reported_accuracy": sm.get("source_reported_accuracy"),
            "kraken_version_validated": mv.kraken_version_validated}


def document_metadata(doc: Document, col: Collection) -> dict:
    """Dublin Core-compatible description of the source document."""
    return {"title": doc.title, "description": doc.description, "type": doc.document_type,
            "script_type": doc.script_type, "languages": doc.languages or [], "date": doc.date_text,
            "date_start": doc.date_start, "date_end": doc.date_end, "archival_reference": doc.archival_reference,
            "provenance": doc.provenance, "notes": doc.notes, "collection": col.title,
            "collection_archival_reference": col.archival_reference, "holding_institution": col.holding_institution,
            "dublin_core": {"dc:title": doc.title, "dc:description": doc.description, "dc:type": doc.document_type,
                            "dc:language": doc.languages or [], "dc:date": doc.date_text,
                            "dc:identifier": doc.archival_reference, "dc:source": doc.provenance,
                            **(doc.dc_metadata or {})}}


def pick_transcription(db, page: Page) -> Transcription | None:
    """Most recently edited transcription of the page."""
    return db.scalar(select(Transcription).where(Transcription.page_id == page.id)
                     .order_by(Transcription.updated_at.desc()).limit(1))


def pick_recognition(db, page: Page) -> Recognition | None:
    return db.scalar(select(Recognition).where(Recognition.page_id == page.id).order_by(Recognition.created_at.desc()).limit(1))


def page_context(db, page: Page, layer: str, *, transcription: Transcription | None = None,
                 recognition: Recognition | None = None, at_revision: int | None = None) -> PageCtx:
    if layer not in LAYERS:
        raise ExportError("invalid_layer", f"layer must be one of {', '.join(LAYERS)}")
    tx = transcription
    rec = recognition or (db.get(Recognition, tx.base_recognition_id) if tx and tx.base_recognition_id else None)
    if tx is None and rec is None:
        raise ExportError("nothing_to_export", f"Page {page.sequence} has neither a transcription nor a recognition")
    if tx is None and layer not in ("htr",):
        # No silent substitution: the caller decides whether HTR-only pages are acceptable.
        raise ExportError("no_transcription", f"Page {page.sequence} has no transcription; only the 'htr' layer exists",
                          {"page_id": str(page.id)})
    seg = db.get(Segmentation, tx.segmentation_id if tx else rec.segmentation_id)
    lines = list(db.scalars(select(TextLine).where(TextLine.segmentation_id == seg.id).order_by(TextLine.order_index)))
    regions = list(db.scalars(select(TextRegion).where(TextRegion.segmentation_id == seg.id).order_by(TextRegion.order_index)))
    htr = {}
    if rec is not None and rec.segmentation_id == seg.id:
        htr = {lr.line_id: lr for lr in db.scalars(select(LineRecognition).where(LineRecognition.recognition_id == rec.id))}
    versions = current_versions(db, tx, at_revision) if tx else {}
    origin = {}
    if tx is not None and tx.origin == "combined":
        from app.models import LineVersion

        rows = db.execute(select(LineVersion.line_id, LineRecognition, ModelVersion)
                          .join(TranscriptionRevision, TranscriptionRevision.id == LineVersion.revision_id)
                          .join(LineRecognition, LineRecognition.id == LineVersion.origin_line_recognition_id)
                          .join(Recognition, Recognition.id == LineRecognition.recognition_id)
                          .join(ModelVersion, ModelVersion.id == Recognition.model_version_id)
                          .where(LineVersion.transcription_id == tx.id, TranscriptionRevision.number == 1)).all()
        for lid, lr, mv in rows:
            origin[lid] = f"{mv.model.slug} {mv.version}"
            htr.setdefault(lid, lr)
    out_lines = []
    stats = {"lines": len(lines), "reviewed": 0, "corrected": 0, "unreviewed": 0, "uncertain": 0, "illegible": 0,
             "chars": 0, "reviewed_chars": 0, "normalized_lines": 0}
    for i, ln in enumerate(lines, 1):
        lr = htr.get(ln.id)
        v = versions.get(ln.id)
        mk = v.text if v else None
        if layer == "htr":
            text = lr.text if lr else ""
        elif layer == "markup":
            text = mk or ""
        elif layer == "normalized":
            text = (v.normalized_text or "") if v else ""
        else:
            text = markup.render(mk, _RENDER[layer]) if mk else ""
        if v:
            stats[v.status.value] += 1
            stats["uncertain"] += int(v.has_uncertain)
            stats["illegible"] += int(v.has_illegible)
            dip = markup.render(v.text, "diplomatic")
            stats["chars"] += len(dip)
            stats["reviewed_chars"] += len(dip) if v.status == LineStatus.REVIEWED else 0
            stats["normalized_lines"] += int(bool(v.normalized_text))
        out_lines.append(LineCtx(id=f"l{i}", n=i, region_id=str(ln.region_id) if ln.region_id else None, bbox=ln.bbox,
                                 baseline=ln.baseline, boundary=ln.boundary, text=text, markup=mk,
                                 htr=lr.text if lr else None, htr_confidence=lr.mean_confidence if lr else None,
                                 normalized=v.normalized_text if v else None, note=v.note if v else None,
                                 status=v.status.value if v else None, origin_model=origin.get(ln.id)))
    region_ctx = []
    rid_map = {}
    for k, r in enumerate(regions, 1):
        rc = RegionCtx(id=f"r{k}", type=r.region_type, boundary=r.boundary)
        rid_map[str(r.id)] = rc
        region_ctx.append(rc)
    orphans = [lc for lc in out_lines if lc.region_id not in rid_map]
    for lc in out_lines:
        if lc.region_id in rid_map:
            rid_map[lc.region_id].line_ids.append(lc.id)
    if orphans:
        # Lines outside any region are grouped in one synthetic block (stated in the provenance).
        rc = RegionCtx(id=f"r{len(region_ctx) + 1}", type="text", boundary=None, line_ids=[lc.id for lc in orphans])
        region_ctx.append(rc)
    for lc in out_lines:
        lc.region_id = next(r.id for r in region_ctx if lc.id in r.line_ids)

    asset = db.get(ImageAsset, seg.input_asset_id)
    original = db.get(ImageAsset, page.image_asset_id)
    seg_mv = db.get(ModelVersion, seg.model_version_id) if seg.model_version_id else None
    seg_info = {"id": str(seg.id), "source": seg.source, "model": _mv_info(seg_mv), "software": seg.software,
                "config": seg.config, "parent_segmentation_id": str(seg.parent_segmentation_id) if seg.parent_segmentation_id else None,
                "created_at": seg.created_at.isoformat(), "synthetic_region": bool(orphans)}
    rec_info = None
    if rec is not None:
        rec_info = {"id": str(rec.id), "model": _mv_info(db.get(ModelVersion, rec.model_version_id)),
                    "software": rec.software, "config": rec.config, "created_at": rec.created_at.isoformat(),
                    "duration_ms": rec.duration_ms, "job_id": str(rec.job_id) if rec.job_id else None}
    tx_info = None
    if tx is not None:
        revs = list(db.scalars(select(TranscriptionRevision).where(TranscriptionRevision.transcription_id == tx.id)
                               .order_by(TranscriptionRevision.number)))
        if at_revision is not None:
            revs = [r for r in revs if r.number <= at_revision]
        author_ids = {r.author_id for r in revs if r.author_id}
        names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(author_ids)))} if author_ids else {}
        tx_info = {"id": str(tx.id), "origin": tx.origin, "policy": tx.policy, "markup_version": tx.markup_version,
                   "revision": revs[-1].number if revs else 0, "revisions": len(revs),
                   "contributors": sorted(set(names.values())),
                   "history": [{"number": r.number, "kind": r.kind, "message": r.message, "changes": r.change_count,
                                "author": names.get(r.author_id), "date": r.created_at.isoformat()} for r in revs],
                   "last_modified": revs[-1].created_at.isoformat() if revs else None}
    quality = {**stats,
               "reviewed_text_share": (stats["reviewed_chars"] / stats["chars"]) if stats["chars"] else None,
               "evaluation": _latest_evaluation(db, page)}
    if tx is not None and rec is not None:
        from app.evaluation import metrics as m

        n = m.Normalization(exclude_gap_lines=False)
        ce = we = 0
        for lc in out_lines:
            if lc.markup is not None and lc.htr is not None:
                a, b = m.normalize(lc.htr, n), m.normalize(markup.render(lc.markup, "diplomatic"), n)
                ce += m.char_distance(a, b)
                we += m.word_distance(a, b)
        quality["corrected_characters"], quality["corrected_words"] = ce, we
    return PageCtx(id=str(page.id), sequence=page.sequence, label=page.label,
                   image={"filename": original.original_filename, "sha256": original.sha256, "width": asset.width,
                          "height": asset.height, "mime_type": original.mime_type,
                          "processed_image_sha256": asset.sha256 if asset.id != original.id else None},
                   text_source=f"transcription r{tx_info['revision']}" if tx_info else "htr", layer=layer,
                   lines=out_lines, regions=region_ctx, segmentation=seg_info, recognition=rec_info,
                   transcription=tx_info, quality=quality)


def _latest_evaluation(db, page: Page) -> list[dict]:
    rows = db.execute(select(EvaluationResult, ModelVersion)
                      .join(GroundTruthPage, GroundTruthPage.id == EvaluationResult.gt_page_id)
                      .join(ModelVersion, ModelVersion.id == EvaluationResult.model_version_id)
                      .where(GroundTruthPage.page_id == page.id).order_by(EvaluationResult.created_at.desc()).limit(10)).all()
    return [{"model": f"{mv.model.slug} {mv.version}", "cer": r.cer, "wer": r.wer, "ref_chars": r.ref_chars,
             "alignment": r.alignment, "methodology": r.methodology, "date": r.created_at.isoformat()} for r, mv in rows]


def document_context(db, doc: Document, pages: list[PageCtx], options: dict, user: User | None) -> DocCtx:
    col = db.get(Collection, doc.collection_id)
    project = db.get(Project, col.project_id)
    return DocCtx(id=str(doc.id), title=doc.title, metadata=document_metadata(doc, col),
                  project={"id": str(project.id), "title": project.title},
                  pages=pages,
                  export={"software": f"SCRIPTORIUM {__version__}", "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "by": user.full_name if user else None, "options": options, "export_id": str(uuid.uuid4())})
