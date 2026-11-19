"""Ground-truth import from ALTO (v2–v4) and PAGE XML (2013, 2019).

Only what the file states is read: regions, lines in document (or PAGE ReadingOrder) order,
baselines, line polygons (when present) and the line text. Coordinates are scaled to the page
image when the file was produced on a proportionally resized copy of it; anything else is refused.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


class ImportError_(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


@dataclass
class GTLine:
    external_id: str | None
    text: str
    baseline: list[list[int]] | None
    boundary: list[list[int]] | None
    bbox: list[int] | None
    region_external_id: str | None = None


@dataclass
class GTRegion:
    external_id: str | None
    region_type: str
    boundary: list[list[int]]


@dataclass
class GTPage:
    format: str
    width: int | None
    height: int | None
    image_filename: str | None
    software: str | None
    regions: list[GTRegion] = field(default_factory=list)
    lines: list[GTLine] = field(default_factory=list)


def _points(s: str | None) -> list[list[float]] | None:
    if not s:
        return None
    nums = [float(x) for x in _NUM.findall(s)]
    if len(nums) < 4 or len(nums) % 2:
        return None
    return [[nums[i], nums[i + 1]] for i in range(0, len(nums), 2)]


def _rect(el) -> list[list[float]] | None:
    try:
        x, y, w, h = (float(el.get(k)) for k in ("HPOS", "VPOS", "WIDTH", "HEIGHT"))
    except (TypeError, ValueError):
        return None
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _local(el) -> str:
    return etree.QName(el).localname


def _children(el, name: str):
    return [c for c in el if isinstance(c.tag, str) and _local(c) == name]


def _desc(el, name: str):
    return [c for c in el.iter() if isinstance(c.tag, str) and _local(c) == name]


def parse(data: bytes) -> GTPage:
    try:
        root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    except etree.XMLSyntaxError as e:
        raise ImportError_("invalid_xml", f"The file is not well-formed XML: {e}") from e
    name = _local(root)
    if name == "alto":
        return _alto(root)
    if name == "PcGts":
        return _page(root)
    raise ImportError_("unsupported_format", f"Root element <{name}> is neither ALTO <alto> nor PAGE <PcGts>")


def _alto(root) -> GTPage:
    pages = _desc(root, "Page")
    if len(pages) != 1:
        raise ImportError_("multi_page_file", f"Expected one <Page>, found {len(pages)}")
    pg = pages[0]
    fn = next((e.text for e in _desc(root, "fileName") if e.text), None)
    sw = next((e.text for e in _desc(root, "softwareName") if e.text), None)
    out = GTPage("alto", _int(pg.get("WIDTH")), _int(pg.get("HEIGHT")), fn, sw)
    for block in _desc(pg, "TextBlock"):
        poly = next((_points(p.get("POINTS")) for p in _desc(block, "Polygon")
                     if p.getparent() is not None and p.getparent().getparent() is block), None) or _rect(block)
        if poly:
            out.regions.append(GTRegion(block.get("ID"), "text", poly))
        for tl in _children(block, "TextLine"):
            parts = []
            for c in tl:
                if not isinstance(c.tag, str):
                    continue
                if _local(c) == "String":
                    parts.append(c.get("CONTENT") or "")
                elif _local(c) == "SP":
                    parts.append(" ")
            text = "".join(parts) if any(_local(c) == "SP" for c in tl if isinstance(c.tag, str)) else " ".join(parts)
            shape = next((_points(p.get("POINTS")) for s in _children(tl, "Shape") for p in _children(s, "Polygon")), None)
            out.lines.append(GTLine(tl.get("ID"), text, _points(tl.get("BASELINE")), shape,
                                    _bbox(_rect(tl) or shape), block.get("ID")))
    return out


def _page(root) -> GTPage:
    pages = _desc(root, "Page")
    if len(pages) != 1:
        raise ImportError_("multi_page_file", f"Expected one <Page>, found {len(pages)}")
    pg = pages[0]
    sw = next((e.text for e in _desc(root, "Creator") if e.text), None)
    out = GTPage("page-xml", _int(pg.get("imageWidth")), _int(pg.get("imageHeight")), pg.get("imageFilename"), sw)
    regions = {r.get("id"): r for r in _desc(pg, "TextRegion")}
    order = [ref.get("regionRef") for ref in _desc(pg, "RegionRefIndexed")]
    order_idx = {}
    for ref in _desc(pg, "RegionRefIndexed"):
        try:
            order_idx[ref.get("regionRef")] = int(ref.get("index"))
        except (TypeError, ValueError):
            pass
    ids = list(regions)
    if order:
        ids = sorted(ids, key=lambda i: (order_idx.get(i, 10**9), ids.index(i)))
    for rid in ids:
        r = regions[rid]
        coords = next((_points(c.get("points")) for c in _children(r, "Coords")), None)
        if coords:
            out.regions.append(GTRegion(rid, r.get("type") or "text", coords))
        for tl in _children(r, "TextLine"):
            boundary = next((_points(c.get("points")) for c in _children(tl, "Coords")), None)
            baseline = next((_points(c.get("points")) for c in _children(tl, "Baseline")), None)
            out.lines.append(GTLine(tl.get("id"), _page_text(tl), baseline, boundary, _bbox(boundary or baseline), rid))
    return out


def _page_text(tl) -> str:
    eqs = _children(tl, "TextEquiv")
    if not eqs:
        return ""

    def key(e):
        try:
            return int(e.get("index", 0))
        except ValueError:
            return 0

    eq = sorted(eqs, key=key)[0]
    uni = _children(eq, "Unicode")
    return (uni[0].text or "") if uni else ""


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _bbox(pts) -> list[float] | None:
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def fit_to_image(gt: GTPage, width: int, height: int) -> dict:
    """Scale coordinates to the stored page image; refuse non-proportional sizes."""
    if not gt.width or not gt.height:
        raise ImportError_("missing_page_size", "The file does not declare the page size; coordinates cannot be verified")
    sx, sy = width / gt.width, height / gt.height
    if abs(sx - sy) > 0.01 * max(sx, sy):
        raise ImportError_("image_mismatch", "The file describes a page whose proportions differ from this page image",
                           {"file_size": [gt.width, gt.height], "image_size": [width, height]})

    def sc(pts):
        if pts is None:
            return None
        return [[round(min(max(x * sx, 0), width - 1)), round(min(max(y * sy, 0), height - 1))] for x, y in pts]

    for r in gt.regions:
        r.boundary = sc(r.boundary)
    for ln in gt.lines:
        ln.baseline, ln.boundary = sc(ln.baseline), sc(ln.boundary)
        ln.bbox = [round(v) for v in _bbox(ln.boundary or ln.baseline)] if (ln.boundary or ln.baseline) else None
    usable = [ln for ln in gt.lines if ln.baseline and len(ln.baseline) >= 2]
    if not usable:
        raise ImportError_("no_lines", "The file contains no line with a baseline")
    return {"scale": [sx, sy], "lines_without_baseline": len(gt.lines) - len(usable)}
