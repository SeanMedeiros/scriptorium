"""Kraken engine: layout analysis, reading order and line recognition.

Thin, explicit wrapper over the supported Kraken 7 Python API
(`kraken.tasks.SegmentationTaskModel`, `kraken.tasks.RecognitionTaskModel`).
This module is imported only by recognition workers.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import logging
import os
import platform
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from PIL import Image

log = logging.getLogger(__name__)

TextDirection = Literal["horizontal-lr", "horizontal-rl", "vertical-lr", "vertical-rl"]


class EngineError(Exception):
    """A processing error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, stage: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.stage, self.details = code, message, stage, details or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "stage": self.stage, "details": self.details}


class Cancelled(Exception):
    pass


@dataclass
class RegionOut:
    external_id: str
    region_type: str
    boundary: list[list[int]]
    tags: dict = field(default_factory=dict)


@dataclass
class LineOut:
    external_id: str
    baseline: list[list[int]] | None
    boundary: list[list[int]] | None
    bbox: list[int]
    region_external_ids: list[str]
    tags: dict = field(default_factory=dict)


@dataclass
class SegmentationOut:
    text_direction: str
    regions: list[RegionOut]
    lines: list[LineOut]  # in reading order
    duration_ms: int
    model: dict


@dataclass
class LineTextOut:
    order_index: int
    text: str
    confidences: list[float]
    cuts: list | None

    @property
    def mean_confidence(self) -> float | None:
        return round(sum(self.confidences) / len(self.confidences), 6) if self.confidences else None


def software_versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for pkg in ("kraken", "torch", "pillow", "numpy", "shapely", "scikit-image"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            pass
    try:
        import torch

        out["device"] = "cuda" if torch.cuda.is_available() else "cpu"
        out["torch_threads"] = str(torch.get_num_threads())
    except Exception:  # noqa: BLE001 — diagnostics only; absence of torch is reported by the missing keys
        out["device"] = "unknown"
    return out


def kraken_version() -> str:
    return md.version("kraken")


def bundled_segmentation_model_path() -> Path:
    return Path(str(resources.files("kraken").joinpath("blla.mlmodel")))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _device_kwargs(device: str) -> dict:
    """Kraken 7 / Lightning: `accelerator` selects cpu|gpu|auto, `device` is the device count ('auto')."""
    threads = int(os.environ.get("SCRIPTORIUM_TORCH_THREADS", "0")) or max(1, (os.cpu_count() or 1))
    return {"accelerator": device if device in ("cpu", "gpu", "auto") else "cpu", "device": "auto", "num_threads": threads}


def _poly(points) -> list[list[int]] | None:
    if points is None:
        return None
    return [[int(round(p[0])), int(round(p[1]))] for p in points]


def _bbox(points: list[list[int]]) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


class _ModelCache:
    """Per-process LRU cache of loaded Kraken task models keyed by (path, sha256)."""

    def __init__(self, capacity: int = 3):
        self.capacity = capacity
        self._items: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, kind: str, path: Path, expected_sha256: str | None):
        from kraken.tasks import RecognitionTaskModel, SegmentationTaskModel

        if not path.is_file():
            raise EngineError("model_file_missing", f"Model file not found: {path}", f"load_{kind}", {"path": str(path)})
        digest = expected_sha256 or sha256_file(path)
        key = (kind, str(path), digest)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        if expected_sha256:
            actual = sha256_file(path)
            if actual != expected_sha256:
                raise EngineError("model_checksum_mismatch", "Model file does not match its registered SHA-256",
                                  f"load_{kind}", {"expected": expected_sha256, "actual": actual})
        t0 = time.perf_counter()
        try:
            model = (SegmentationTaskModel if kind == "segmentation" else RecognitionTaskModel).load_model(str(path))
        except Exception as e:  # noqa: BLE001
            raise EngineError("model_load_failed", f"Kraken could not load the {kind} model: {e}", f"load_{kind}",
                              {"path": str(path), "exception": type(e).__name__}) from e
        log.info("model loaded", extra={"kind": kind, "path": str(path), "ms": round((time.perf_counter() - t0) * 1000)})
        with self._lock:
            self._items[key] = model
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return model


_cache = _ModelCache(capacity=int(os.environ.get("SCRIPTORIUM_MODEL_CACHE_SIZE", "3")))


def describe_model(path: Path) -> dict:
    """Load a model file with Kraken and report what the FILE itself declares.
    Used for compatibility validation; raises EngineError if Kraken cannot load it."""
    from kraken.models import load_models

    try:
        nets = load_models(str(path))
    except Exception as e:  # noqa: BLE001
        raise EngineError("model_load_failed", f"Kraken cannot load this file: {e}", "validate",
                          {"exception": type(e).__name__}) from e
    out = []
    for n in nets:
        meta = getattr(n, "user_metadata", {}) or {}
        info = {
            "class": type(n).__name__,
            "model_type": list(getattr(n, "model_type", []) or []),
            "seg_type": getattr(n, "seg_type", None),
            "one_channel_mode": getattr(n, "one_channel_mode", None),
            "vgsl": meta.get("vgsl"),
            "hyper_params": meta.get("hyper_params"),
            "metrics_count": len(meta.get("metrics") or []),
            "accuracy_history_len": len(meta.get("accuracy") or []),
            "legacy_polygons": meta.get("legacy_polygons"),
            "class_mapping": meta.get("class_mapping"),
        }
        codec = getattr(n, "codec", None)
        if codec is not None and hasattr(codec, "c2l"):
            info["alphabet_size"] = len(codec.c2l)
            info["alphabet"] = "".join(sorted(k for k in codec.c2l if isinstance(k, str) and len(k) == 1))
        out.append(info)
    return {"networks": out, "kraken_version": kraken_version()}


def segment(im: Image.Image, *, model_path: Path, model_sha256: str | None = None,
            text_direction: TextDirection = "horizontal-lr", device: str = "cpu") -> SegmentationOut:
    """Baseline layout analysis (regions, lines, reading order) with a Kraken segmentation model."""
    from kraken.configs import SegmentationInferenceConfig

    task = _cache.get("segmentation", model_path, model_sha256)
    cfg = SegmentationInferenceConfig(text_direction=text_direction, **_device_kwargs(device))
    t0 = time.perf_counter()
    try:
        seg = task.predict(im, cfg)
    except Exception as e:  # noqa: BLE001
        raise EngineError("segmentation_failed", f"Kraken segmentation failed: {e}", "segmentation",
                          {"exception": type(e).__name__}) from e
    ms = int((time.perf_counter() - t0) * 1000)

    regions: list[RegionOut] = []
    for rtype, regs in (seg.regions or {}).items():
        for r in regs:
            boundary = _poly(r.boundary)
            if boundary:
                regions.append(RegionOut(str(r.id), str(rtype), boundary, dict(r.tags or {})))
    lines: list[LineOut] = []
    for ln in seg.lines:
        boundary = _poly(getattr(ln, "boundary", None))
        baseline = _poly(getattr(ln, "baseline", None))
        pts = boundary or baseline
        if not pts:
            continue
        lines.append(LineOut(str(ln.id), baseline, boundary, _bbox(pts), [str(x) for x in (ln.regions or [])],
                             {k: v for k, v in (ln.tags or {}).items()}))
    return SegmentationOut(text_direction=seg.text_direction, regions=regions, lines=lines, duration_ms=ms,
                           model={"path": str(model_path)})


def build_kraken_segmentation(lines: list[dict], regions: list[dict], text_direction: str, imagename: str = "page"):
    """Rebuild a kraken `Segmentation` from stored (possibly human-edited) geometry."""
    from kraken.containers import BaselineLine, Region, Segmentation

    kregions: dict[str, list] = {}
    for r in regions:
        kregions.setdefault(r["region_type"], []).append(
            Region(id=r["external_id"], boundary=[tuple(p) for p in r["boundary"]], tags=r.get("tags") or None))
    klines = []
    for ln in lines:
        if not ln.get("baseline") or not ln.get("boundary"):
            raise EngineError("invalid_segmentation", "Baseline recognition needs a baseline and a boundary polygon for every line",
                              "recognition", {"line": ln.get("external_id")})
        klines.append(BaselineLine(id=ln["external_id"], baseline=[tuple(p) for p in ln["baseline"]],
                                   boundary=[tuple(p) for p in ln["boundary"]], regions=ln.get("region_external_ids") or None,
                                   tags=ln.get("tags") or None))
    return Segmentation(type="baselines", imagename=imagename, text_direction=text_direction, script_detection=False,
                        lines=klines, regions=kregions)


def recognize(im: Image.Image, kseg, *, model_path: Path, model_sha256: str | None = None, device: str = "cpu",
              should_cancel: Callable[[], bool] | None = None,
              on_progress: Callable[[int, int], None] | None = None) -> Iterator[LineTextOut]:
    """Recognise every line of `kseg` in reading order. Yields one result per line.
    The exact Unicode sequence produced by the model is returned without any normalisation."""
    from kraken.configs import RecognitionInferenceConfig

    task = _cache.get("recognition", model_path, model_sha256)
    if task.seg_type not in (None, "baselines"):
        raise EngineError("segmentation_type_mismatch", f"Model expects '{task.seg_type}' segmentation, got baselines",
                          "recognition", {"model_seg_type": task.seg_type})
    cfg = RecognitionInferenceConfig(num_line_workers=0, raise_on_error=True, **_device_kwargs(device))
    total = len(kseg.lines)
    try:
        for i, rec in enumerate(task.predict(im, kseg, cfg)):
            if should_cancel and should_cancel():
                raise Cancelled()
            cuts = rec.cuts
            yield LineTextOut(order_index=i, text=rec.prediction,
                              confidences=[round(float(c), 6) for c in rec.confidences],
                              cuts=[[list(map(int, p)) for p in c] for c in cuts] if cuts and isinstance(cuts[0], (list, tuple)) else None)
            if on_progress:
                on_progress(i + 1, total)
    except (Cancelled, EngineError):
        raise
    except Exception as e:  # noqa: BLE001
        raise EngineError("recognition_failed", f"Kraken recognition failed: {e}", "recognition",
                          {"exception": type(e).__name__}) from e


def compute_boundaries(im: Image.Image, baselines: list[list[list[int]]]) -> list[list[list[int]] | None]:
    """Line polygons for hand-drawn baselines, computed by Kraken's own polygoniser
    (`kraken.lib.segmentation.calculate_polygonal_environment`)."""
    from kraken.lib.segmentation import calculate_polygonal_environment

    try:
        polys = calculate_polygonal_environment(im.convert("L"), [[tuple(p) for p in bl] for bl in baselines])
    except Exception as e:  # noqa: BLE001
        raise EngineError("polygonization_failed", f"Kraken could not compute line polygons: {e}", "segmentation",
                          {"exception": type(e).__name__}) from e
    return [_poly(p) if p is not None else None for p in polys]
