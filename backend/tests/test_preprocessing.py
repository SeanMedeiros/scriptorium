import hashlib
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from app.recognition.preprocessing import Pipeline, estimate_skew, map_points, run_pipeline


def _page(skew: float = 0.0) -> Image.Image:
    im = Image.new("RGB", (900, 700), (236, 226, 205))
    d = ImageDraw.Draw(im)
    for y in range(60, 660, 36):
        d.rectangle([70, y, 830, y + 7], fill=(70, 50, 30))
    return im.rotate(skew, expand=True, fillcolor=(236, 226, 205), resample=Image.Resampling.BICUBIC) if skew else im


def _png(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def test_pipeline_is_deterministic_and_does_not_touch_input():
    src = _page(2.0)
    before = _png(src)
    p = Pipeline.model_validate({"operations": [{"op": "grayscale"}, {"op": "deskew"}, {"op": "autocontrast", "cutoff": 2},
                                                {"op": "denoise", "size": 3}, {"op": "sharpen"}]})
    a, prov_a = run_pipeline(src, p)
    b, prov_b = run_pipeline(src, p)
    assert hashlib.sha256(_png(a)).digest() == hashlib.sha256(_png(b)).digest()
    assert prov_a == prov_b and prov_a["libraries"]["pillow"]
    assert _png(src) == before, "input image must not be modified"


@pytest.mark.parametrize("applied", [-3.0, 1.5, 4.2])
def test_deskew_recovers_known_rotation(applied):
    est = estimate_skew(_page(applied), max_angle=5, step=0.1)
    assert abs(est + applied) <= 0.15


def test_geometric_provenance_maps_back_to_original_coordinates():
    src = Image.new("L", (500, 300), 255)
    ImageDraw.Draw(src).rectangle([400, 50, 404, 54], fill=0)  # dot centred at (402, 52)
    out, prov = run_pipeline(src, Pipeline.model_validate({"operations": [{"op": "rotate", "angle": 11.0}]}))
    assert prov["geometry_preserving"] is False
    ys, xs = np.where(np.asarray(out) < 128)
    (x, y), = map_points([[xs.mean(), ys.mean()]], prov["affine_to_parent"])
    assert abs(x - 402) < 1.5 and abs(y - 52) < 1.5


def test_photometric_pipeline_preserves_geometry():
    _, prov = run_pipeline(_page(), Pipeline.model_validate({"operations": [{"op": "grayscale"}, {"op": "equalize"}]}))
    assert prov["geometry_preserving"] is True and prov["affine_to_parent"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


@pytest.mark.kraken
def test_binarization_uses_kraken_nlbin():
    # Realistic grey-level page (gradient + noise); Kraken leaves already-bitonal images untouched by design.
    rng = np.random.default_rng(0)
    base = np.asarray(_page().convert("L"), dtype=np.float32)
    grad = np.linspace(-25, 25, base.shape[1])[None, :]
    page = Image.fromarray(np.clip(base + grad + rng.normal(0, 8, base.shape), 0, 255).astype(np.uint8))
    out, prov = run_pipeline(page, Pipeline.model_validate({"operations": [{"op": "binarize"}]}))
    assert set(np.unique(np.asarray(out))) <= {0, 255} and prov["libraries"]["kraken"]


def test_invalid_operations_are_rejected():
    with pytest.raises(ValidationError):
        Pipeline.model_validate({"operations": [{"op": "magic_enhance"}]})
    with pytest.raises(ValidationError):
        Pipeline.model_validate({"operations": [{"op": "denoise", "size": 99}]})
