"""Offline validation of exported XML against the vendored official schemas.

* ALTO  → ALTO 4.4 XSD                  * PAGE → PAGE 2019-07-15 XSD
* TEI   → TEI P5 `tei_all` RELAX NG     * hOCR → no formal schema exists; a structural check of
  the hOCR 1.2 conventions is applied and reported as such (never as schema validation).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from lxml import etree

SCHEMAS = Path(__file__).parent / "schemas"
SCHEMA_INFO = {
    "alto": {"schema": "ALTO 4.4 (XSD)", "file": "alto-4-4.xsd"},
    "page": {"schema": "PAGE 2019-07-15 (XSD)", "file": "page-2019-07-15.xsd"},
    "tei": {"schema": "TEI P5 4.12.0 tei_all (RELAX NG)", "file": "tei_all.rng"},
}


@lru_cache(maxsize=None)
def _validator(kind: str):
    path = SCHEMAS / SCHEMA_INFO[kind]["file"]
    doc = etree.parse(str(path))
    return etree.RelaxNG(doc) if path.suffix == ".rng" else etree.XMLSchema(doc)


@lru_cache(maxsize=None)
def _iiif_schema() -> dict:
    import json

    return json.loads((SCHEMAS / "iiif_3_0.json").read_text())


def validate_iiif(manifest: dict) -> dict:
    """IIIF Presentation 3.0, with the JSON Schema of the official IIIF presentation validator."""
    import jsonschema

    v = jsonschema.Draft7Validator(_iiif_schema())
    errors = sorted({f"{'/'.join(map(str, e.absolute_path))}: {e.message[:200]}" for e in v.iter_errors(manifest)})
    return {"format": "iiif", "schema": "IIIF Presentation 3.0 (iiif_3_0.json, presentation-validator)",
            "valid": not errors, "errors": errors[:50], "method": "schema validation"}


def validate(kind: str, data: bytes) -> dict:
    if kind == "hocr":
        return check_hocr(data)
    v = _validator(kind)
    doc = etree.fromstring(data)
    ok = v.validate(doc)
    errors = [f"line {e.line}: {e.message}" for e in v.error_log][:50]
    return {"format": kind, "schema": SCHEMA_INFO[kind]["schema"], "valid": bool(ok), "errors": errors,
            "method": "schema validation"}


_BBOX = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")


def check_hocr(data: bytes) -> dict:
    errors = []
    try:
        doc = etree.fromstring(data)
    except etree.XMLSyntaxError as e:
        return {"format": "hocr", "schema": None, "valid": False, "errors": [f"not well-formed XHTML: {e}"],
                "method": "structural check (hOCR has no formal schema)"}
    ns = {"h": "http://www.w3.org/1999/xhtml"}
    metas = {m.get("name"): m.get("content") for m in doc.iterfind(".//h:meta", ns)}
    for req in ("ocr-system", "ocr-capabilities"):
        if req not in metas:
            errors.append(f"missing <meta name='{req}'>")
    caps = set((metas.get("ocr-capabilities") or "").split())
    pages = doc.findall(".//h:*[@class='ocr_page']", ns)
    if not pages:
        errors.append("no ocr_page element")
    for pg in pages:
        m = _BBOX.search(pg.get("title", ""))
        if not m:
            errors.append(f"ocr_page {pg.get('id')} has no bbox")
            continue
        px0, py0, px1, py1 = map(int, m.groups())
        for el in pg.iter():
            cls = el.get("class")
            if not cls or not cls.startswith("ocr"):
                continue
            if cls not in caps and cls != "ocr_page":
                errors.append(f"class {cls} used but not declared in ocr-capabilities")
            if not el.get("id"):
                errors.append(f"{cls} without id")
            mm = _BBOX.search(el.get("title", ""))
            if not mm:
                errors.append(f"{cls} {el.get('id')} has no bbox")
                continue
            x0, y0, x1, y1 = map(int, mm.groups())
            if not (px0 <= x0 <= x1 <= px1 and py0 <= y0 <= y1 <= py1):
                errors.append(f"{cls} {el.get('id')} bbox outside its page")
    ids = [e.get("id") for e in doc.iter() if e.get("id")]
    if len(ids) != len(set(ids)):
        errors.append("duplicate ids")
    return {"format": "hocr", "schema": None, "valid": not errors, "errors": sorted(set(errors))[:50],
            "method": "structural check of hOCR 1.2 conventions (hOCR has no formal schema)"}
