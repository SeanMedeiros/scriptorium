"""Export builders: TXT, JSON, ALTO 4.4, PAGE 2019, hOCR 1.2, TEI P5, DOCX, PDF.

Every builder receives a `DocCtx` (one document, one or more pages) and returns bytes. ALTO and
PAGE describe exactly one page per file (the batch exporter writes one file per page).
Coordinates are page-image pixels of the image the segmentation was computed on.
"""
from __future__ import annotations

import logging
import io
import json
import re
from dataclasses import asdict
from pathlib import Path

from lxml import etree
from pydantic import BaseModel

from app.editorial import markup
from app.exports.context import DocCtx, LineCtx, PageCtx

FORMATS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "json": ("application/json", "json"),
    "alto": ("application/xml", "alto.xml"),
    "page": ("application/xml", "page.xml"),
    "hocr": ("application/xhtml+xml", "hocr.html"),
    "tei": ("application/tei+xml", "tei.xml"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": ("application/pdf", "pdf"),
}
PER_PAGE = {"alto", "page"}
MARKUP_LAYERS_OK = {"txt", "json"}


class TeiMapping(BaseModel):
    """Configurable TEI mapping (documented in docs/tei-mapping.md)."""
    abbreviations: str = "choice"  # choice → <choice><abbr/><expan/></choice> | abbr | expan
    uncertain_cert: bool = False  # add cert="low" to <unclear>
    include_facsimile: bool = True  # <facsimile> zones, @facs on <pb>/<lb>
    include_normalized: bool = False  # lines with a normalised layer → <choice><orig/><reg/></choice>
    include_notes: bool = True  # line notes → <note type="editorial">


# ------------------------------------------------------------------------------------------ helpers
def _poly(ln: LineCtx) -> list:
    if ln.boundary:
        return ln.boundary
    x0, y0, x1, y1 = ln.bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _bbox_of(points: list) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _region_poly(page: PageCtx, region) -> list:
    if region.boundary:
        return region.boundary
    pts = [p for ln in page.lines if ln.region_id == region.id for p in _poly(ln)]
    x0, y0, x1, y1 = _bbox_of(pts)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _pts(points: list) -> str:
    return " ".join(f"{int(round(x))},{int(round(y))}" for x, y in points)


def _xml(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _model_label(page: PageCtx) -> str:
    m = (page.recognition or {}).get("model")
    return f"{m['name']} {m['version']}" if m else "unknown model"


def _source_line(page: PageCtx) -> str:
    if page.text_source == "htr":
        return "uncorrected HTR output"
    return f"{page.text_source}, layer {page.layer}"


# --------------------------------------------------------------------------------------------- TXT
def to_txt(doc: DocCtx) -> bytes:
    out = []
    md = doc.metadata
    out.append(doc.title)
    if md.get("archival_reference"):
        out.append(md["archival_reference"])
    out.append(f"Exported by {doc.export['software']} on {doc.export['date']}")
    for p in doc.pages:
        out.append("")
        out.append(f"=== Page {p.sequence}{f' ({p.label})' if p.label else ''} — {_source_line(p)} — model: {_model_label(p)}")
        for ln in p.lines:
            out.append(ln.text)
    return ("\n".join(out) + "\n").encode("utf-8")


# -------------------------------------------------------------------------------------------- JSON
def to_json(doc: DocCtx) -> bytes:
    data = {"format": "scriptorium-export/1", "document": {"id": doc.id, "title": doc.title, "metadata": doc.metadata},
            "project": doc.project, "export": doc.export, "pages": []}
    for p in doc.pages:
        d = asdict(p)
        d["markup_note"] = "line.markup uses SCRIPTORIUM editorial markup v1 (see docs/researcher-guide.md)"
        data["pages"].append(d)
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# -------------------------------------------------------------------------------------------- ALTO
ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"


def to_alto(doc: DocCtx, page: PageCtx) -> bytes:
    A = "{%s}" % ALTO_NS
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    root = etree.Element(A + "alto", nsmap={None: ALTO_NS, "xsi": xsi, "xlink": "http://www.w3.org/1999/xlink"})
    root.set("{%s}schemaLocation" % xsi, f"{ALTO_NS} http://www.loc.gov/standards/alto/v4/alto-4-4.xsd")
    root.set("SCHEMAVERSION", "4.4")
    desc = etree.SubElement(root, A + "Description")
    etree.SubElement(desc, A + "MeasurementUnit").text = "pixel"
    sii = etree.SubElement(desc, A + "sourceImageInformation")
    etree.SubElement(sii, A + "fileName").text = page.image.get("filename") or f"page-{page.sequence}"
    k = 0
    rec = page.recognition
    if rec:
        k += 1
        pr = etree.SubElement(desc, A + "Processing", ID=f"proc{k}")
        etree.SubElement(pr, A + "processingCategory").text = "contentGeneration"
        etree.SubElement(pr, A + "processingDateTime").text = rec["created_at"]
        m = rec.get("model") or {}
        etree.SubElement(pr, A + "processingStepDescription").text = (
            f"Text recognition with model {m.get('name')} {m.get('version')} (DOI {m.get('doi')}, sha256 {m.get('sha256')})")
        sw = etree.SubElement(pr, A + "processingSoftware")
        etree.SubElement(sw, A + "softwareCreator").text = "Benjamin Kiessling et al."
        etree.SubElement(sw, A + "softwareName").text = "Kraken"
        etree.SubElement(sw, A + "softwareVersion").text = str((rec.get("software") or {}).get("kraken", ""))
    if page.transcription:
        k += 1
        pr = etree.SubElement(desc, A + "Processing", ID=f"proc{k}")
        etree.SubElement(pr, A + "processingCategory").text = "contentModification"
        etree.SubElement(pr, A + "processingDateTime").text = page.transcription["last_modified"] or doc.export["date"]
        etree.SubElement(pr, A + "processingAgency").text = ", ".join(page.transcription["contributors"]) or "researchers"
        etree.SubElement(pr, A + "processingStepDescription").text = (
            f"Human correction, revision {page.transcription['revision']}; exported layer '{page.layer}'")
        sw = etree.SubElement(pr, A + "processingSoftware")
        etree.SubElement(sw, A + "softwareName").text = "SCRIPTORIUM"
        etree.SubElement(sw, A + "softwareVersion").text = doc.export["software"].split()[-1]
    layout = etree.SubElement(root, A + "Layout")
    w, h = page.image["width"], page.image["height"]
    pg = etree.SubElement(layout, A + "Page", ID=f"p{page.sequence}", PHYSICAL_IMG_NR=str(page.sequence),
                          WIDTH=str(w), HEIGHT=str(h))
    if page.label:
        pg.set("PRINTED_IMG_NR", page.label)
    ps = etree.SubElement(pg, A + "PrintSpace", HPOS="0", VPOS="0", WIDTH=str(w), HEIGHT=str(h))
    for r in page.regions:
        poly = _region_poly(page, r)
        x0, y0, x1, y1 = _bbox_of(poly)
        tb = etree.SubElement(ps, A + "TextBlock", ID=f"p{page.sequence}_{r.id}", HPOS=str(x0), VPOS=str(y0),
                              WIDTH=str(x1 - x0), HEIGHT=str(y1 - y0))
        sh = etree.SubElement(tb, A + "Shape")
        etree.SubElement(sh, A + "Polygon", POINTS=_pts(poly))
        for ln in (x for x in page.lines if x.region_id == r.id):
            lx0, ly0, lx1, ly1 = _bbox_of(_poly(ln))
            tl = etree.SubElement(tb, A + "TextLine", ID=f"p{page.sequence}_{ln.id}", HPOS=str(lx0), VPOS=str(ly0),
                                  WIDTH=str(lx1 - lx0), HEIGHT=str(ly1 - ly0))
            if ln.baseline:
                tl.set("BASELINE", _pts(ln.baseline))
            sh = etree.SubElement(tl, A + "Shape")
            etree.SubElement(sh, A + "Polygon", POINTS=_pts(_poly(ln)))
            # Words carry no coordinates of their own (the platform stores line geometry); no WC is
            # written because the engine reports line-level, uncalibrated confidences only.
            words = ln.text.split() or [""]
            for i, wd in enumerate(words):
                if i:
                    etree.SubElement(tl, A + "SP")
                etree.SubElement(tl, A + "String", ID=f"p{page.sequence}_{ln.id}_w{i + 1}", CONTENT=wd)
    return _xml(root)


# -------------------------------------------------------------------------------------------- PAGE
PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


def to_page(doc: DocCtx, page: PageCtx) -> bytes:
    P = "{%s}" % PAGE_NS
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    root = etree.Element(P + "PcGts", nsmap={None: PAGE_NS, "xsi": xsi})
    root.set("{%s}schemaLocation" % xsi, f"{PAGE_NS} {PAGE_NS}/pagecontent.xsd")
    md = etree.SubElement(root, P + "Metadata")
    etree.SubElement(md, P + "Creator").text = doc.export["software"]
    created = (page.recognition or {}).get("created_at") or doc.export["date"]
    etree.SubElement(md, P + "Created").text = created
    etree.SubElement(md, P + "LastChange").text = (page.transcription or {}).get("last_modified") or created
    etree.SubElement(md, P + "Comments").text = (
        f"{doc.title} · page {page.sequence} · {_source_line(page)} · recognition model {_model_label(page)}"
        + ("; TextEquiv index 1 = exported layer, index 2 = original HTR output" if page.layer != "htr" else ""))
    rec = page.recognition
    if rec and rec.get("model"):
        m = rec["model"]
        it = etree.SubElement(md, P + "MetadataItem", type="processingStep", name="text-recognition",
                              value=f"Kraken {(rec.get('software') or {}).get('kraken', '')}")
        labels = etree.SubElement(it, P + "Labels")
        for k, v in (("model", m.get("name")), ("version", m.get("version")), ("doi", m.get("doi")),
                     ("sha256", m.get("sha256")), ("license", m.get("license"))):
            if v:
                etree.SubElement(labels, P + "Label", value=str(v), type=k)
    pg = etree.SubElement(root, P + "Page", imageFilename=page.image.get("filename") or f"page-{page.sequence}",
                          imageWidth=str(page.image["width"]), imageHeight=str(page.image["height"]))
    ro = etree.SubElement(pg, P + "ReadingOrder")
    og = etree.SubElement(ro, P + "OrderedGroup", id=f"ro_p{page.sequence}", caption="Reading order")
    for i, r in enumerate(page.regions):
        etree.SubElement(og, P + "RegionRefIndexed", index=str(i), regionRef=f"p{page.sequence}_{r.id}")
    for r in page.regions:
        tr = etree.SubElement(pg, P + "TextRegion", id=f"p{page.sequence}_{r.id}", type="paragraph")
        etree.SubElement(tr, P + "Coords", points=_pts(_region_poly(page, r)))
        texts = []
        for ln in (x for x in page.lines if x.region_id == r.id):
            tl = etree.SubElement(tr, P + "TextLine", id=f"p{page.sequence}_{ln.id}")
            etree.SubElement(tl, P + "Coords", points=_pts(_poly(ln)))
            if ln.baseline:
                etree.SubElement(tl, P + "Baseline", points=_pts(ln.baseline))
            te = etree.SubElement(tl, P + "TextEquiv", index="1")
            etree.SubElement(te, P + "Unicode").text = ln.text
            if page.layer != "htr" and ln.htr is not None:
                te2 = etree.SubElement(tl, P + "TextEquiv", index="2", comments="original HTR output")
                etree.SubElement(te2, P + "Unicode").text = ln.htr
            texts.append(ln.text)
        te = etree.SubElement(tr, P + "TextEquiv")
        etree.SubElement(te, P + "Unicode").text = "\n".join(texts)
    return _xml(root)


# -------------------------------------------------------------------------------------------- hOCR
XHTML = "http://www.w3.org/1999/xhtml"


def _baseline_hocr(ln: LineCtx) -> str | None:
    if not ln.baseline or len(ln.baseline) < 2:
        return None
    (x1, y1), (x2, y2) = ln.baseline[0], ln.baseline[-1]
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    bx0, _, _, by1 = _bbox_of(_poly(ln))
    offset = (y1 + slope * (bx0 - x1)) - by1
    return f"baseline {slope:.4f} {offset:.0f}"


def to_hocr(doc: DocCtx) -> bytes:
    H = "{%s}" % XHTML
    lang = (doc.metadata.get("languages") or [None])[0]
    root = etree.Element(H + "html", nsmap={None: XHTML})
    if lang:
        root.set("lang", lang)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    head = etree.SubElement(root, H + "head")
    etree.SubElement(head, H + "title").text = doc.title
    etree.SubElement(head, H + "meta", {"http-equiv": "Content-Type", "content": "text/html; charset=utf-8"})
    kraken = next((str((p.recognition or {}).get("software", {}).get("kraken")) for p in doc.pages if p.recognition), "")
    etree.SubElement(head, H + "meta", name="ocr-system", content=f"{doc.export['software']} / Kraken {kraken}".strip())
    etree.SubElement(head, H + "meta", name="ocr-capabilities", content="ocr_page ocr_carea ocr_par ocr_line")
    etree.SubElement(head, H + "meta", name="ocr-number-of-pages", content=str(len(doc.pages)))
    body = etree.SubElement(root, H + "body")
    for p in doc.pages:
        w, h = p.image["width"], p.image["height"]
        pg = etree.SubElement(body, H + "div", {"class": "ocr_page", "id": f"page_{p.sequence}",
                                                 "title": f'image "{p.image.get("filename") or ""}"; bbox 0 0 {w} {h}; ppageno {p.sequence - 1}'})
        for r in p.regions:
            x0, y0, x1, y1 = _bbox_of(_region_poly(p, r))
            ca = etree.SubElement(pg, H + "div", {"class": "ocr_carea", "id": f"block_{p.sequence}_{r.id}",
                                                   "title": f"bbox {x0} {y0} {x1} {y1}"})
            par = etree.SubElement(ca, H + "p", {"class": "ocr_par", "id": f"par_{p.sequence}_{r.id}",
                                                 "title": f"bbox {x0} {y0} {x1} {y1}"})
            for ln in (x for x in p.lines if x.region_id == r.id):
                lx0, ly0, lx1, ly1 = _bbox_of(_poly(ln))
                title = f"bbox {lx0} {ly0} {lx1} {ly1}"
                if bl := _baseline_hocr(ln):
                    title += f"; {bl}"
                span = etree.SubElement(par, H + "span", {"class": "ocr_line", "id": f"line_{p.sequence}_{ln.id}",
                                                          "title": title})
                span.text = ln.text
    doctype = "<!DOCTYPE html>"
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True, doctype=doctype)


# --------------------------------------------------------------------------------------------- TEI
TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def _add_text(parent, text: str) -> None:
    if not text:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _tei_nodes(parent, nodes, mapping: TeiMapping) -> None:
    T = "{%s}" % TEI_NS
    for n in nodes:
        if isinstance(n, markup.Text):
            _add_text(parent, n.value)
        elif isinstance(n, markup.Gap):
            g = etree.SubElement(parent, T + "gap", reason="illegible")
            if n.extent is not None:
                g.set("unit", "chars")
                g.set("quantity", str(n.extent))
        elif isinstance(n, markup.Abbr):
            if mapping.abbreviations == "abbr":
                etree.SubElement(parent, T + "abbr").text = n.abbr
            elif mapping.abbreviations == "expan":
                etree.SubElement(parent, T + "expan").text = n.expansion
            else:
                ch = etree.SubElement(parent, T + "choice")
                etree.SubElement(ch, T + "abbr").text = n.abbr
                etree.SubElement(ch, T + "expan").text = n.expansion
        else:
            tag = {"uncertain": "unclear", "addition": "supplied", "deletion": "del", "superscript": "hi"}[n.kind]
            el = etree.SubElement(parent, T + tag)
            if n.kind == "superscript":
                el.set("rend", "superscript")
            if n.kind == "uncertain" and mapping.uncertain_cert:
                el.set("cert", "low")
            _tei_nodes(el, n.children, mapping)


def to_tei(doc: DocCtx, mapping: TeiMapping | None = None) -> bytes:
    mapping = mapping or TeiMapping()
    T = "{%s}" % TEI_NS
    md = doc.metadata
    root = etree.Element(T + "TEI", nsmap={None: TEI_NS})
    header = etree.SubElement(root, T + "teiHeader")
    fd = etree.SubElement(header, T + "fileDesc")
    ts = etree.SubElement(fd, T + "titleStmt")
    etree.SubElement(ts, T + "title").text = doc.title
    contributors = sorted({c for p in doc.pages if p.transcription for c in p.transcription["contributors"]})
    if contributors:
        rs = etree.SubElement(ts, T + "respStmt")
        etree.SubElement(rs, T + "resp").text = "Transcription and correction"
        for c in contributors:
            etree.SubElement(rs, T + "persName").text = c
    models = {}
    for p in doc.pages:
        m = (p.recognition or {}).get("model")
        if m:
            models[m["model_version_id"]] = (m, (p.recognition.get("software") or {}).get("kraken"))
    for m, _ in models.values():
        rs = etree.SubElement(ts, T + "respStmt")
        etree.SubElement(rs, T + "resp").text = "Automatic text recognition"
        etree.SubElement(rs, T + "name").text = f"{m['name']} {m['version']}"
    pub = etree.SubElement(fd, T + "publicationStmt")
    etree.SubElement(pub, T + "p").text = (f"Exported from {doc.export['software']} on {doc.export['date']} "
                                           f"(project “{doc.project['title']}”). Not published.")
    sd = etree.SubElement(fd, T + "sourceDesc")
    if md.get("archival_reference"):
        ms = etree.SubElement(sd, T + "msDesc")
        mi = etree.SubElement(ms, T + "msIdentifier")
        etree.SubElement(mi, T + "idno").text = md["archival_reference"]
        if md.get("description") or md.get("date") or md.get("provenance"):
            if md.get("description"):
                ct = etree.SubElement(ms, T + "msContents")
                etree.SubElement(ct, T + "p").text = md["description"]
            hist = etree.SubElement(ms, T + "history")
            if md.get("date") or md.get("date_start"):
                orig = etree.SubElement(hist, T + "origin")
                od = etree.SubElement(orig, T + "origDate")
                od.text = md.get("date") or str(md.get("date_start"))
                if md.get("date_start"):
                    od.set("notBefore", f"{md['date_start']:04d}")
                if md.get("date_end"):
                    od.set("notAfter", f"{md['date_end']:04d}")
            if md.get("provenance"):
                etree.SubElement(etree.SubElement(hist, T + "provenance"), T + "p").text = md["provenance"]
            if not len(hist):
                ms.remove(hist)
    else:
        b = etree.SubElement(sd, T + "bibl")
        etree.SubElement(b, T + "title").text = doc.title
        if md.get("date"):
            etree.SubElement(b, T + "date").text = md["date"]
    enc = etree.SubElement(header, T + "encodingDesc")
    pd = etree.SubElement(enc, T + "projectDesc")
    etree.SubElement(pd, T + "p").text = f"Transcribed in the SCRIPTORIUM project “{doc.project['title']}”."
    ed = etree.SubElement(enc, T + "editorialDecl")
    policies = {json.dumps(p.transcription["policy"], sort_keys=True) for p in doc.pages if p.transcription}
    for pol in sorted(policies):
        pj = json.loads(pol)
        etree.SubElement(ed, T + "p").text = ("Transcription policy: " + ", ".join(f"{k}={v}" for k, v in pj.items()))
    etree.SubElement(ed, T + "p").text = (
        "Editorial markup mapping: uncertain reading → unclear; illegible → gap[@reason='illegible']; editorial "
        f"addition → supplied; deletion in the source → del; abbreviation → {mapping.abbreviations}; superscript → "
        "hi[@rend='superscript']. Pages marked 'uncorrected HTR output' were not reviewed.")
    if models:
        ai = etree.SubElement(enc, T + "appInfo")
        kv = next((k for _, k in models.values() if k), None)
        if kv:
            app_ = etree.SubElement(ai, T + "application", ident="kraken", version=str(kv))
            etree.SubElement(app_, T + "label").text = "Kraken"
        for m, _ in models.values():
            app_ = etree.SubElement(ai, T + "application", ident=f"model-{m['slug']}")
            # @version must be a TEI version number (e.g. 1.6.2); other labels (e.g. "V1") go in <label>.
            ver = str(m["version"]).lstrip("vV")
            if re.fullmatch(r"\d+(\.\d+){0,2}", ver):
                app_.set("version", ver)
            etree.SubElement(app_, T + "label").text = f"{m['name']} ({m['version']})"
            if m.get("doi"):
                etree.SubElement(app_, T + "ptr", target=f"https://doi.org/{m['doi']}")
    if md.get("languages"):
        prof = etree.SubElement(header, T + "profileDesc")
        lu = etree.SubElement(prof, T + "langUsage")
        for lang in md["languages"]:
            etree.SubElement(lu, T + "language", ident=lang).text = lang
    hist = [h for p in doc.pages if p.transcription for h in p.transcription["history"]]
    if hist:
        rd = etree.SubElement(header, T + "revisionDesc")
        lc = etree.SubElement(rd, T + "listChange")
        for p in doc.pages:
            for h in (p.transcription or {}).get("history", []):
                ch = etree.SubElement(lc, T + "change", when=h["date"][:10])
                ch.text = f"Page {p.sequence}, revision {h['number']} ({h['kind']}{', ' + h['author'] if h['author'] else ''})" + (
                    f": {h['message']}" if h["message"] else "")
    if mapping.include_facsimile:
        fac = etree.SubElement(root, T + "facsimile")
        for p in doc.pages:
            surf = etree.SubElement(fac, T + "surface", ulx="0", uly="0", lrx=str(p.image["width"]), lry=str(p.image["height"]))
            surf.set(XML_ID, f"facs_p{p.sequence}")
            etree.SubElement(surf, T + "graphic", url=p.image.get("filename") or f"page-{p.sequence}",
                             width=f"{p.image['width']}px", height=f"{p.image['height']}px")
            for ln in p.lines:
                z = etree.SubElement(surf, T + "zone", points=_pts(_poly(ln)))
                z.set(XML_ID, f"z_p{p.sequence}_{ln.id}")
    text = etree.SubElement(root, T + "text")
    body = etree.SubElement(text, T + "body")
    div = etree.SubElement(body, T + "div")
    for p in doc.pages:
        pb = etree.SubElement(div, T + "pb", n=p.label or str(p.sequence))
        if mapping.include_facsimile:
            pb.set("facs", f"#facs_p{p.sequence}")
        if p.text_source == "htr":
            etree.SubElement(div, T + "note", type="status").text = "Uncorrected HTR output"
        ab = etree.SubElement(div, T + "ab")
        for ln in p.lines:
            lb = etree.SubElement(ab, T + "lb", n=str(ln.n))
            if mapping.include_facsimile:
                lb.set("facs", f"#z_p{p.sequence}_{ln.id}")
            target = ab
            if mapping.include_normalized and ln.normalized:
                ch = etree.SubElement(ab, T + "choice")
                target = etree.SubElement(ch, T + "orig")
            if ln.markup is not None and p.layer != "htr":
                _tei_nodes(target, markup.parse(ln.markup), mapping)
            else:
                _add_text(target, ln.htr if p.layer == "htr" else ln.text)
            if target is not ab:
                etree.SubElement(target.getparent(), T + "reg").text = ln.normalized
            if mapping.include_notes and ln.note:
                etree.SubElement(ab, T + "note", type="editorial").text = ln.note
    return _xml(root)


# ----------------------------------------------------------------------------------- DOCX and PDF
def _provenance_lines(doc: DocCtx) -> list[str]:
    out = [f"Exported by {doc.export['software']} on {doc.export['date']}" + (f" by {doc.export['by']}" if doc.export["by"] else "")]
    seen = set()
    for p in doc.pages:
        m = (p.recognition or {}).get("model")
        if m and m["model_version_id"] not in seen:
            seen.add(m["model_version_id"])
            out.append(f"Recognition model: {m['name']} {m['version']} — DOI {m.get('doi') or 'n/a'}, licence "
                       f"{m.get('license') or 'n/a'}, file sha256 {m.get('sha256')}; Kraken "
                       f"{(p.recognition.get('software') or {}).get('kraken')}")
            if m.get("authors"):
                out.append(f"Model authors: {', '.join(m['authors'])}")
    return out


def _meta_rows(doc: DocCtx) -> list[tuple[str, str]]:
    md = doc.metadata
    rows = [("Archival reference", md.get("archival_reference")), ("Date", md.get("date")),
            ("Languages", ", ".join(md.get("languages") or [])), ("Type", md.get("type")),
            ("Collection", md.get("collection")), ("Provenance", md.get("provenance"))]
    return [(k, v) for k, v in rows if v]


def to_docx(doc: DocCtx) -> bytes:
    from docx import Document as Docx
    from docx.shared import Pt

    d = Docx()
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(11)
    d.core_properties.title = doc.title
    d.core_properties.comments = f"Exported by {doc.export['software']}"
    d.add_heading(doc.title, level=1)
    rows = _meta_rows(doc)
    if rows:
        t = d.add_table(rows=0, cols=2)
        for k, v in rows:
            c = t.add_row().cells
            c[0].text, c[1].text = k, str(v)
    for p in doc.pages:
        d.add_heading(f"Page {p.sequence}{f' ({p.label})' if p.label else ''}", level=2)
        info = d.add_paragraph()
        info.add_run(f"{_source_line(p)} · recognition model {_model_label(p)}").italic = True
        for ln in p.lines:
            para = d.add_paragraph()
            para.paragraph_format.space_after = Pt(0)
            num = para.add_run(f"{ln.n:>3}  ")
            num.font.size = Pt(8)
            para.add_run(ln.text)
        notes = [(ln.n, ln.note) for ln in p.lines if ln.note]
        if notes:
            d.add_paragraph("Notes", style="Heading 3")
            for n, note in notes:
                d.add_paragraph(f"l. {n}: {note}")
    d.add_heading("Provenance", level=2)
    for line in _provenance_lines(doc):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path(__file__).parent / "fonts"]


def _font(name: str) -> Path:
    for d in FONT_DIRS:
        if (d / name).exists():
            return d / name
    raise FileNotFoundError(f"Font {name} not found (install fonts-dejavu-core)")


def to_pdf(doc: DocCtx) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("Serif", "", str(_font("DejaVuSerif.ttf")))
    pdf.add_font("Serif", "B", str(_font("DejaVuSerif-Bold.ttf")))
    pdf.add_font("Serif", "I", str(_font("DejaVuSerif-Italic.ttf")) if (FONT_DIRS[0] / "DejaVuSerif-Italic.ttf").exists()
                 else str(_font("DejaVuSerif.ttf")))
    try:
        pdf.set_text_shaping(True)  # correct placement of combining marks (NFD text) — needs uharfbuzz
    except Exception:  # noqa: BLE001 — fpdf2 without uharfbuzz: fall back to unshaped text
        logging.getLogger("scriptorium.exports").warning("PDF text shaping unavailable; combining marks may be misplaced")
    pdf.set_title(doc.title)
    pdf.set_creator(doc.export["software"])
    pdf.add_page()
    pdf.set_font("Serif", "B", 15)
    pdf.multi_cell(0, 8, doc.title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Serif", "", 9.5)
    for k, v in _meta_rows(doc):
        pdf.multi_cell(0, 5, f"{k}: {v}", new_x="LMARGIN", new_y="NEXT")
    for p in doc.pages:
        pdf.ln(4)
        pdf.set_font("Serif", "B", 12)
        pdf.multi_cell(0, 7, f"Page {p.sequence}{f' ({p.label})' if p.label else ''}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Serif", "I", 8.5)
        pdf.multi_cell(0, 5, f"{_source_line(p)} · recognition model {_model_label(p)}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Serif", "", 11)
        for ln in p.lines:
            pdf.set_font("Serif", "", 7.5)
            pdf.cell(10, 6, str(ln.n), align="R")
            pdf.set_font("Serif", "", 11)
            pdf.multi_cell(0, 6, ln.text or " ", new_x="LMARGIN", new_y="NEXT")
        notes = [(ln.n, ln.note) for ln in p.lines if ln.note]
        if notes:
            pdf.set_font("Serif", "B", 9.5)
            pdf.multi_cell(0, 6, "Notes", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Serif", "", 9.5)
            for n, note in notes:
                pdf.multi_cell(0, 5, f"l. {n}: {note}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Serif", "B", 10)
    pdf.multi_cell(0, 6, "Provenance", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Serif", "", 8.5)
    for line in _provenance_lines(doc):
        pdf.multi_cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def build(fmt: str, doc: DocCtx, page: PageCtx | None = None, tei: TeiMapping | None = None) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(fmt)
    if fmt in PER_PAGE:
        return (to_alto if fmt == "alto" else to_page)(doc, page or doc.pages[0])
    return {"txt": to_txt, "json": to_json, "hocr": to_hocr, "docx": to_docx, "pdf": to_pdf}.get(fmt, None)(doc) \
        if fmt != "tei" else to_tei(doc, tei)
