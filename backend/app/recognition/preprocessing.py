"""Reproducible image preprocessing.

A pipeline is an ordered list of operations with explicit parameters. Running the
same pipeline on the same input bytes with the same library versions yields the
same output bytes. Originals are never modified: the result is stored as a new
DERIVED asset whose `derivation` records the pipeline, the library versions and,
for geometric operations, the affine transform back to the parent's coordinates.
"""
from __future__ import annotations

import importlib.metadata as md
import math
from typing import Annotated, Literal, Union

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel, Field

PIPELINE_SCHEMA_VERSION = 1


class Grayscale(BaseModel):
    op: Literal["grayscale"] = "grayscale"


class AutoContrast(BaseModel):
    """Linear stretch of the histogram, ignoring `cutoff` percent at each end."""
    op: Literal["autocontrast"] = "autocontrast"
    cutoff: float = Field(1.0, ge=0, le=20)


class Equalize(BaseModel):
    op: Literal["equalize"] = "equalize"


class Denoise(BaseModel):
    """Median filter (odd kernel size)."""
    op: Literal["denoise"] = "denoise"
    size: int = Field(3, ge=3, le=9)


class Sharpen(BaseModel):
    """Unsharp mask."""
    op: Literal["sharpen"] = "sharpen"
    radius: float = Field(2.0, gt=0, le=10)
    percent: int = Field(120, ge=10, le=500)
    threshold: int = Field(3, ge=0, le=50)


class Rotate(BaseModel):
    """Rotation correction by an explicit angle (degrees, counter-clockwise)."""
    op: Literal["rotate"] = "rotate"
    angle: float = Field(..., ge=-360, le=360)


class Deskew(BaseModel):
    """Estimate the skew angle by projection-profile analysis and rotate it away."""
    op: Literal["deskew"] = "deskew"
    max_angle: float = Field(5.0, gt=0, le=15)
    step: float = Field(0.1, ge=0.02, le=1)


class Binarize(BaseModel):
    """Kraken's non-linear binarisation (nlbin)."""
    op: Literal["binarize"] = "binarize"
    threshold: float = Field(0.5, gt=0, lt=1)
    zoom: float = Field(0.5, gt=0, le=1)
    escale: float = Field(1.0, gt=0, le=10)
    border: float = Field(0.1, ge=0, le=0.5)
    perc: int = Field(80, ge=1, le=100)
    range: int = Field(20, ge=1, le=200)
    low: int = Field(5, ge=0, le=100)
    high: int = Field(90, ge=0, le=100)


Operation = Annotated[
    Union[Grayscale, AutoContrast, Equalize, Denoise, Sharpen, Rotate, Deskew, Binarize], Field(discriminator="op")
]


class Pipeline(BaseModel):
    operations: list[Operation] = Field(default_factory=list, max_length=20)

    @property
    def is_identity(self) -> bool:
        return not self.operations


def library_versions() -> dict[str, str]:
    out = {}
    for pkg in ("pillow", "numpy", "scipy", "kraken"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            pass
    return out


def _to_gray_array(im: Image.Image, max_side: int = 1200) -> np.ndarray:
    g = im.convert("L")
    scale = min(1.0, max_side / max(g.size))
    if scale < 1.0:
        g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))), Image.Resampling.BILINEAR)
    return np.asarray(g, dtype=np.float32)


def estimate_skew(im: Image.Image, max_angle: float = 5.0, step: float = 0.1) -> float:
    """Return the rotation (degrees, counter-clockwise) that best aligns text rows.

    The page is downscaled, inverted and Otsu-thresholded; for each candidate angle
    the variance of the horizontal projection profile is computed. Text rows give
    the sharpest profile when horizontal. Deterministic.
    """
    from scipy import ndimage

    from skimage.filters import threshold_otsu

    a = _to_gray_array(im)
    if float(a.max() - a.min()) < 1.0:
        return 0.0
    t = float(threshold_otsu(a))
    ink = (a <= t).astype(np.float32)
    if ink.mean() < 1e-4:
        return 0.0
    best, best_score = 0.0, -1.0
    n = int(round(max_angle / step))
    for i in range(-n, n + 1):
        ang = i * step
        rot = ndimage.rotate(ink, ang, reshape=False, order=0, mode="constant", cval=0)
        score = float(np.var(rot.sum(axis=1)))
        if score > best_score + 1e-9:
            best, best_score = ang, score
    return round(best, 3)


def _rotate(im: Image.Image, angle: float) -> tuple[Image.Image, list[list[float]]]:
    """Rotate counter-clockwise with canvas expansion; returns image and the affine
    matrix (2x3) mapping output pixel coordinates back to input coordinates."""
    if abs(angle) < 1e-9:
        return im, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    fill = 255 if im.mode in ("L", "1") else (255,) * len(im.getbands())
    out = im.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
    w, h = im.size
    W, H = out.size
    th = math.radians(angle)
    c, s = math.cos(th), math.sin(th)
    # Output → input: translate to out centre, rotate by +angle (image y axis points down), translate to in centre.
    cx, cy, Cx, Cy = w / 2, h / 2, W / 2, H / 2
    a, b = c, -s
    d, e = s, c
    tx = cx - a * Cx - b * Cy
    ty = cy - d * Cx - e * Cy
    return out, [[a, b, tx], [d, e, ty]]


def map_points(points: list[list[float]], affine: list[list[float]]) -> list[list[float]]:
    (a, b, c), (d, e, f) = affine
    return [[a * x + b * y + c, d * x + e * y + f] for x, y in points]


def compose(first: list[list[float]], then: list[list[float]]) -> list[list[float]]:
    """Affine of applying `then` (out→mid) followed by `first` (mid→in)."""
    A = np.array(first + [[0, 0, 1]], dtype=float)
    B = np.array(then + [[0, 0, 1]], dtype=float)
    return (A @ B)[:2].tolist()


def run_pipeline(im: Image.Image, pipeline: Pipeline) -> tuple[Image.Image, dict]:
    """Apply the pipeline. Returns the new image and a provenance record."""
    applied: list[dict] = []
    affine = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]  # output → input
    cur = im
    for op in pipeline.operations:
        rec = op.model_dump()
        if isinstance(op, Grayscale):
            cur = cur.convert("L")
        elif isinstance(op, AutoContrast):
            cur = ImageOps.autocontrast(cur if cur.mode in ("L", "RGB") else cur.convert("RGB"), cutoff=op.cutoff)
        elif isinstance(op, Equalize):
            cur = ImageOps.equalize(cur if cur.mode in ("L", "RGB") else cur.convert("RGB"))
        elif isinstance(op, Denoise):
            cur = cur.filter(ImageFilter.MedianFilter(size=op.size | 1))
        elif isinstance(op, Sharpen):
            cur = cur.filter(ImageFilter.UnsharpMask(radius=op.radius, percent=op.percent, threshold=op.threshold))
        elif isinstance(op, Rotate):
            cur, m = _rotate(cur, op.angle)
            affine = compose(affine, m)
        elif isinstance(op, Deskew):
            angle = estimate_skew(cur, op.max_angle, op.step)
            rec["estimated_angle"] = angle
            cur, m = _rotate(cur, angle)
            affine = compose(affine, m)
        elif isinstance(op, Binarize):
            from kraken.binarization import nlbin

            cur = nlbin(cur, threshold=op.threshold, zoom=op.zoom, escale=op.escale, border=op.border,
                        perc=op.perc, range=op.range, low=op.low, high=op.high).convert("L")
        applied.append(rec)
    provenance = {
        "schema": PIPELINE_SCHEMA_VERSION,
        "operations": applied,
        "geometry_preserving": affine == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "affine_to_parent": affine,
        "libraries": library_versions(),
        "output_size": list(cur.size),
        "output_mode": cur.mode,
    }
    return cur, provenance
