"""Batch export of a document or a collection as a ZIP archive with a manifest (master prompt §13).

Archive layout::

    manifest.json                 export options, per-file SHA-256, validation results, skipped pages
    README.txt
    <document>/<document>.<ext>   txt, json, hocr.html, tei.xml, docx, pdf (one file per document)
    <document>/alto/p0001.alto.xml, <document>/page/p0001.page.xml   (one file per page)
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import traceback
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from app.exports import context as ctx
from app.exports import formats as fm
from app.exports import validate as val
from app.models import Collection, Document, Export, Page, User

XML_VALIDATED = {"alto", "page", "tei", "hocr"}


def slug(s: str, n: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")
    return (s or "document")[:n]


def documents_in_scope(db, export: Export) -> list[Document]:
    if export.scope == "document":
        d = db.get(Document, export.scope_id)
        return [d] if d else []
    return list(db.scalars(select(Document).where(Document.collection_id == export.scope_id).order_by(Document.created_at)))


def build_document(db, doc: Document, formats: list[str], options: dict, user: User | None):
    """Return ([(path, bytes, format)], skipped, page_sources) for one document."""
    layer = options.get("layer", "corrected")
    include_uncorrected = options.get("include_uncorrected", True)
    pages, skipped, sources = [], [], []
    for page in db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.sequence)):
        tx = ctx.pick_transcription(db, page) if layer != "htr" else None
        rec = None if tx else ctx.pick_recognition(db, page)
        if tx is None and rec is None:
            skipped.append({"document": doc.title, "page": page.sequence, "reason": "no recognition or transcription"})
            continue
        if tx is None and layer != "htr" and not include_uncorrected:
            skipped.append({"document": doc.title, "page": page.sequence, "reason": "no transcription (uncorrected pages excluded)"})
            continue
        pc = ctx.page_context(db, page, layer if tx else "htr", transcription=tx, recognition=rec)
        pages.append(pc)
        sources.append({"document": doc.title, "page": page.sequence, "text_source": pc.text_source, "layer": pc.layer})
    if not pages:
        return [], skipped, sources
    dc = ctx.document_context(db, doc, pages, options, user)
    tei = fm.TeiMapping.model_validate(options.get("tei") or {})
    base = slug(doc.title)
    files = []
    for f in formats:
        ext = fm.FORMATS[f][1]
        if f in fm.PER_PAGE:
            for pc in pages:
                files.append((f"{base}/{f}/p{pc.sequence:04d}.{ext}", fm.build(f, dc, pc), f))
        else:
            files.append((f"{base}/{base}.{ext}", fm.build(f, dc, tei=tei), f))
    return files, skipped, sources


def _training_data_zip(db, export: Export, storage) -> tuple[tempfile.SpooledTemporaryFile, dict]:
    """Kraken/eScriptorium-ready PAGE XML of every ground-truth split of a dataset, with images."""
    from pathlib import Path

    from app.models import EvaluationDataset
    from app.training.service import write_page_dataset

    ds = db.get(EvaluationDataset, export.scope_id)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        man = write_page_dataset(db, ds, storage, work, lambda *a, **k: None,
                                 normalization=export.options.get("normalization"), seed=None,
                                 creator=f"SCRIPTORIUM training-data export {export.id}",
                                 splits=("train", "validation", "test"), relative_images=True)
        man.update({"format": "scriptorium-training-data/1", "export_id": str(export.id),
                    "usage": "ketos train -f page -t train.txt -e validation.txt   (test.txt: keep for evaluation only)"})
        buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        files = []
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in sorted(work.rglob("*")):
                if f.is_file():
                    rel = str(f.relative_to(work))
                    data = f.read_bytes()
                    entry = {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    if rel.endswith(".xml"):
                        entry["validation"] = val.validate("page", data)
                    files.append(entry)
                    z.writestr(rel, data)
            man["files"] = files
            z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=2, default=str))
        buf.seek(0)
        return buf, man


def run_export(db, export_id: uuid.UUID, storage) -> Export:
    export = db.get(Export, export_id)
    export.status = "processing"
    db.commit()
    if export.scope == "training-data":
        try:
            buf, man = _training_data_zip(db, export, storage)
            try:
                obj = storage.put_stream("exports", buf)
            finally:
                buf.close()  # release the spooled temporary file immediately
            invalid = [f["path"] for f in man["files"] if f.get("validation") and not f["validation"]["valid"]]
            export.storage_key, export.sha256, export.size_bytes = obj.key, obj.sha256, obj.size
            export.summary = {"files": len(man["files"]), "pages": sum(len(v["pages"]) for v in man["splits"].values()),
                              "lines": {k: v["lines"] for k, v in man["splits"].items()},
                              "excluded_lines": man["excluded_lines"],
                              "validation": {"checked": sum(1 for f in man["files"] if f.get("validation")), "invalid": invalid}}
            export.status = "completed"
        except Exception as e:  # noqa: BLE001
            db.rollback()
            export = db.get(Export, export_id)
            export.status = "failed"
            export.error = {"code": "export_failed", "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
        export.completed_at = datetime.now(timezone.utc)
        db.commit()
        return export
    try:
        user = db.get(User, export.created_by_id) if export.created_by_id else None
        manifest = {"format": "scriptorium-export-manifest/1", "export_id": str(export.id),
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "scope": export.scope, "scope_id": str(export.scope_id), "formats": export.formats,
                    "options": export.options, "files": [], "skipped_pages": [], "pages": []}
        if export.scope == "collection":
            col = db.get(Collection, export.scope_id)
            manifest["collection"] = col.title if col else None
        buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        used = set()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for doc in documents_in_scope(db, export):
                files, skipped, sources = build_document(db, doc, export.formats, export.options, user)
                manifest["skipped_pages"] += skipped
                manifest["pages"] += sources
                for path, data, f in files:
                    if path in used:  # two documents with the same title
                        path = path.replace("/", f"-{str(doc.id)[:8]}/", 1)
                    used.add(path)
                    entry = {"path": path, "format": f, "document": doc.title, "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()}
                    if f in XML_VALIDATED:
                        entry["validation"] = val.validate(f, data)
                    manifest["files"].append(entry)
                    z.writestr(path, data)
            invalid = [f["path"] for f in manifest["files"] if f.get("validation") and not f["validation"]["valid"]]
            manifest["validation_summary"] = {"checked": sum(1 for f in manifest["files"] if f.get("validation")),
                                              "invalid": invalid}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr("README.txt", README)
        buf.seek(0)
        try:
            obj = storage.put_stream("exports", buf)
        finally:
            buf.close()
        export.storage_key, export.sha256, export.size_bytes = obj.key, obj.sha256, obj.size
        export.summary = {"files": len(manifest["files"]), "documents": len({f["document"] for f in manifest["files"]}),
                          "pages": len(manifest["pages"]), "skipped_pages": manifest["skipped_pages"],
                          "uncorrected_pages": sum(1 for p in manifest["pages"] if p["text_source"] == "htr"),
                          "validation": manifest["validation_summary"]}
        export.status = "completed" if manifest["files"] else "failed"
        if not manifest["files"]:
            export.error = {"code": "nothing_to_export", "message": "No page in this scope has exportable text"}
        export.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:  # noqa: BLE001 — every failure is reported on the export
        db.rollback()
        export = db.get(Export, export_id)
        export.status = "failed"
        export.error = {"code": "export_failed", "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
        export.completed_at = datetime.now(timezone.utc)
        db.commit()
    return export


README = """SCRIPTORIUM export
==================

manifest.json lists every file with its SHA-256 checksum, the validation result of each XML file
(ALTO 4.4 XSD, PAGE 2019-07-15 XSD, TEI P5 tei_all RELAX NG; hOCR has no formal schema and is
structurally checked), the text source of every page (a transcription revision, or the uncorrected
HTR output) and the pages that were skipped.

Coordinates are pixels of the page image named in each file. Model provenance (Zenodo DOI, file
checksum, licence, Kraken version) is included in every format.
"""


def zip_bytes(files: list[tuple[str, bytes]]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for p, d in files:
            z.writestr(p, d)
    return b.getvalue()
