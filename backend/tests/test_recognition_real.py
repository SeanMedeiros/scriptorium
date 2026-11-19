"""REAL HTR integration tests.

These tests run Kraken 7.1.1 with the real TraPrInq model (downloaded from Zenodo,
MD5-verified) and Kraken's bundled segmenter on a real 16th-century page from the
Portuguese Inquisition (Torre do Tombo, PT/TT/TSO-IL/28/7926, image m0052), taken
from the TraPrInq validation set (Zenodo record 13986218, CC-BY-4.0).
Nothing is mocked. They are slow (≈1 min per full page on 2 CPU cores).
"""
import io
import json
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import Recognition, RecognitionJob
from app.recognition import engine
from tests.conftest import sample_page

pytestmark = [pytest.mark.integration, pytest.mark.kraken]
API = "/api/v1"


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _alto_text(xml_path: Path) -> str:
    ns = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}
    lines = []
    for tl in ET.parse(xml_path).getroot().iter("{%s}TextLine" % ns["a"]):
        words = [s.get("CONTENT", "") for s in tl.iter("{%s}String" % ns["a"])]
        lines.append(" ".join(words))
    return "\n".join(lines)


def test_engine_segments_and_recognises_a_real_manuscript_page(traprinq, blla):
    path = sample_page()
    im = Image.open(path)
    im.load()
    seg = engine.segment(im, model_path=Path(blla.file_path), model_sha256=blla.sha256)
    assert len(seg.lines) >= 15, "a full page of text must yield many baselines"
    for ln in seg.lines:
        assert len(ln.baseline) >= 2 and len(ln.boundary) >= 3
        x0, y0, x1, y1 = ln.bbox
        assert 0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height
    kseg = engine.build_kraken_segmentation([vars(l) for l in seg.lines], [vars(r) for r in seg.regions], seg.text_direction)
    out = list(engine.recognize(im, kseg, model_path=Path(traprinq.file_path), model_sha256=traprinq.sha256))
    assert len(out) == len(seg.lines)
    hyp = "\n".join(o.text for o in out)
    assert sum(len(o.text) for o in out) > 300
    for o in out:
        assert len(o.confidences) == len(o.text)
        assert all(0.0 <= c <= 1.0 for c in o.confidences)
    # Sanity check only (proper CER/WER evaluation is a Phase 5 feature): page-level
    # character edit rate against the Transkribus ground truth, both NFD-normalised
    # as documented for TraPrInq, whitespace collapsed.
    gt = _alto_text(path.with_suffix(".xml"))
    norm = lambda s: " ".join(unicodedata.normalize("NFD", s).split())  # noqa: E731
    rate = _levenshtein(norm(hyp), norm(gt)) / max(1, len(norm(gt)))
    print(f"\n[sanity] page-level character edit rate vs ground truth: {rate:.3f} ({len(out)} lines)")
    assert rate < 0.5, "recognition output is unrelated to the page — the model is not working"


def _upload_sample(u, p, c, path: Path):
    r = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
               files=[("files", (path.name, path.read_bytes(), "image/jpeg"))],
               data={"metadata": json.dumps({"languages": ["por"], "archival_reference": "PT/TT/TSO-IL/28/7926",
                                             "date_text": "16th century", "script_type": "manuscript"})})
    assert r.status_code == 201, r.text
    return u.get(f"{API}/documents/{r.json()[0]['id']}").json()


def test_full_job_through_the_api_persists_traceable_outputs(make_user, traprinq, blla, db):
    path = sample_page()
    u = make_user("paleographer@uc.pt")
    p = u.post(f"{API}/projects", json={"title": "Inquisição de Lisboa", "languages": ["por"]}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Processos"}).json()
    doc = _upload_sample(u, p, c, path)
    page = doc["pages"][0]

    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={
        "page_ids": [page["id"]], "recognition_model_version_id": str(traprinq.id),
        "preprocessing": {"operations": [{"op": "grayscale"}]}})
    assert r.status_code == 201, r.text
    job = u.get(f"{API}/recognition-jobs/{r.json()['jobs'][0]['id']}").json()
    assert job["status"] == "completed", job.get("error")
    assert job["progress"] == 1.0 and job["attempts"] == 1 and job["worker"]
    messages = [e["message"] for e in job["log"]]
    for m in ("job started", "preprocessing applied", "segmentation finished", "recognition finished", "job completed"):
        assert m in messages

    # Segmentation: coordinates in the space of the preprocessed (derived) image, which is recorded.
    seg = u.get(f"{API}/segmentations/{job['output_segmentation_id']}").json()
    assert seg["input_asset"]["kind"] == "derived"
    assert seg["input_asset"]["parent_asset_id"] == page["image_asset_id"]
    assert seg["input_asset"]["derivation"]["geometry_preserving"] is True
    assert seg["software"]["kraken"] == "7.1.1" and len(seg["lines"]) >= 15

    rec = u.get(f"{API}/recognitions/{job['output_recognition_id']}").json()
    assert rec["model"].startswith("TraPrInq") and rec["unicode_normalization"] == "NFD"
    assert rec["line_count"] == len(seg["lines"]) and rec["char_count"] > 300
    assert "NOT a calibrated probability" in rec["confidence_note"]
    line_by_id = {ln["id"]: ln for ln in seg["lines"]}
    for lr in rec["lines"]:
        assert lr["line_id"] in line_by_id  # every text line is tied to image coordinates

    # Provenance chain in the database: text → line → segmentation → page/model/software → job.
    r_row = db.get(Recognition, rec["id"])
    assert r_row.model_version_id == traprinq.id and r_row.job_id is not None
    assert db.get(RecognitionJob, r_row.job_id).recognition_model_version_id == traprinq.id

    # Original HTR output is immutable.
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE line_recognitions SET text = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE text_lines SET bbox = '[0,0,1,1]'"))
    db.rollback()

    # Re-recognise the SAME segmentation (job_type=recognize): a new immutable output, the first one untouched.
    r2 = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={
        "job_type": "recognize", "page_ids": [page["id"]], "input_segmentation_id": seg["id"],
        "recognition_model_version_id": str(traprinq.id)}).json()
    j2 = u.get(f"{API}/recognition-jobs/{r2['jobs'][0]['id']}").json()
    assert j2["status"] == "completed" and j2["output_segmentation_id"] == seg["id"]
    recs = u.get(f"{API}/pages/{page['id']}/recognitions").json()
    assert len(recs) == 2
    first = u.get(f"{API}/recognitions/{rec['id']}").json()
    assert [l["text"] for l in first["lines"]] == [l["text"] for l in rec["lines"]]
    # Deterministic inference: identical text for identical input, model and segmentation.
    second = u.get(f"{API}/recognitions/{j2['output_recognition_id']}").json()
    assert [l["text"] for l in second["lines"]] == [l["text"] for l in rec["lines"]]

    audit = u.get(f"{API}/audit-logs", params={"project_id": p["id"]}).json()["items"]
    assert {"recognition.submitted", "recognition.completed", "document.upload"} <= {a["action"] for a in audit}


def test_genuine_processing_error_is_reported(make_user, traprinq, blla):
    """A blank sheet: Kraken finds no lines; the job fails with an explicit, stable error."""
    u = make_user("errors@uc.pt")
    p = u.post(f"{API}/projects", json={"title": "Errors"}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Blank"}).json()
    b = io.BytesIO()
    Image.new("L", (800, 1000), 250).save(b, format="PNG")
    d = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
               files=[("files", ("blank.png", b.getvalue(), "image/png"))]).json()[0]
    page = u.get(f"{API}/documents/{d['id']}").json()["pages"][0]
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
               json={"page_ids": [page["id"]], "recognition_model_version_id": str(traprinq.id)}).json()
    job = u.get(f"{API}/recognition-jobs/{r['jobs'][0]['id']}").json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "no_lines_detected" and job["error"]["stage"] == "segmentation"
    assert job["output_segmentation_id"] is None and job["output_recognition_id"] is None
    assert any(e["level"] == "error" for e in job["log"])
    assert u.get(f"{API}/pages/{page['id']}/segmentations").json() == []  # no partial output

    # Retry produces a new job linked to the failed one.
    rr = u.post(f"{API}/recognition-jobs/{job['id']}/retry")
    assert rr.status_code == 201 and rr.json()["retry_of_id"] == job["id"]
    assert u.get(f"{API}/recognition-jobs/{rr.json()['id']}").json()["status"] == "failed"


def test_adjusted_segmentation_is_recognised_with_kraken_polygons(make_user, traprinq, blla, db):
    """Researcher adjusts the layout (drops spurious lines, keeps a hand-drawn baseline without
    polygon); Kraken computes the missing polygon and TraPrInq recognises the adjusted layout."""
    path = sample_page()
    u = make_user("segeditor@uc.pt")
    p = u.post(f"{API}/projects", json={"title": "Seg"}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "C"}).json()
    page = _upload_sample(u, p, c, path)["pages"][0]
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={"page_ids": [page["id"]], "job_type": "segment"}).json()
    job = u.get(f"{API}/recognition-jobs/{r['jobs'][0]['id']}").json()
    assert job["status"] == "completed"
    seg = u.get(f"{API}/segmentations/{job['output_segmentation_id']}").json()
    # Keep the 6 longest lines; the 3rd is redrawn as a bare baseline (no polygon).
    longest = sorted(seg["lines"], key=lambda ln: ln["bbox"][2] - ln["bbox"][0], reverse=True)[:6]
    longest.sort(key=lambda ln: ln["order_index"])
    lines = [{"baseline": ln["baseline"], "boundary": None if i == 2 else ln["boundary"], "source_line_id": ln["id"]}
             for i, ln in enumerate(longest)]
    manual = u.post(f"{API}/pages/{page['id']}/segmentations",
                    json={"parent_segmentation_id": seg["id"], "lines": lines, "message": "kept main text lines"}).json()
    assert manual["lines_without_boundary"] == 1
    r2 = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={
        "job_type": "recognize", "page_ids": [page["id"]], "input_segmentation_id": manual["id"],
        "recognition_model_version_id": str(traprinq.id)}).json()
    j2 = u.get(f"{API}/recognition-jobs/{r2['jobs'][0]['id']}").json()
    assert j2["status"] == "completed", j2["error"]
    assert any("computing line polygons with Kraken" in e["message"] for e in j2["log"])
    derived = u.get(f"{API}/segmentations/{j2['output_segmentation_id']}").json()
    assert derived["source"] == "kraken" and derived["id"] != manual["id"]
    assert derived["config"]["operation"] == "calculate_polygonal_environment"
    assert len(derived["lines"]) == 6 and all(len(ln["boundary"]) >= 3 for ln in derived["lines"])
    rec = u.get(f"{API}/recognitions/{j2['output_recognition_id']}").json()
    assert rec["line_count"] == 6 and len(rec["lines"][2]["text"]) > 5  # the redrawn line was recognised
    # The manual segmentation itself is unchanged (still without the polygon).
    m = u.get(f"{API}/segmentations/{manual['id']}").json()
    assert m["source"] == "manual" and m["lines"][2]["boundary"] is None
