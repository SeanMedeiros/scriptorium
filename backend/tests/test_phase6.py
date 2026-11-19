"""Phase 6 (labelled-fixture and real-file tests): institutional model upload and scoping, annotation
management and W3C export, IIIF manifests, analytics, training-data export.

The `ws` fixture is a LABELLED FIXTURE recognition (not Kraken output). Model uploads use REAL Kraken
model files (the bundled blla segmenter, CATMuS Medieval 1.5.0 from Zenodo) and real validation by Kraken.
"""
import io
import json
import zipfile

import pytest

from app.recognition import engine
from tests.test_research import _review_all
from tests.test_transcriptions import ws  # noqa: F401

API = "/api/v1"


def test_manual_model_upload_is_validated_by_kraken_and_scoped(ws, make_user, catmus):  # noqa: F811
    pi, p = ws["pi"], ws["project"]
    src = open(catmus.file_path, "rb").read()
    r = pi.post(f"{API}/projects/{p['id']}/institutional-models",
                data={"name": "CATMuS institutional copy", "version": "1.5.0-local", "license_id": "cc-by-4.0",
                      "unicode_normalization": "NFD"},
                files={"file": ("catmus-medieval.mlmodel", src, "application/octet-stream")})
    assert r.status_code == 201, r.text
    mv = pi.get(f"{API}/institutional-models/{r.json()['id']}").json()
    assert mv["status"] == "validated" and mv["source"] == "manual" and mv["owner_project_id"] == p["id"]
    assert mv["kraken_version_validated"] == engine.kraken_version()
    # A segmentation network uploaded as a recognition model is a genuine error, reported as such.
    bad = pi.post(f"{API}/projects/{p['id']}/institutional-models", data={"name": "Wrong type", "version": "1"},
                  files={"file": ("blla.mlmodel", engine.bundled_segmentation_model_path().read_bytes(), "application/octet-stream")})
    assert bad.status_code == 201
    assert pi.get(f"{API}/institutional-models/{bad.json()['id']}").json()["status"] == "incompatible"
    assert pi.post(f"{API}/projects/{p['id']}/institutional-models", data={"name": "Not a model", "version": "1"},
                   files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 422
    # Listing: visible in its project, invisible to an outsider (list and direct access).
    assert [m["id"] for m in pi.get(f"{API}/projects/{p['id']}/institutional-models").json()] == [mv["id"], bad.json()["id"]]
    out = make_user("out@ox.ac.uk")
    assert all(m["id"] != mv["id"] for m in out.get(f"{API}/models").json())
    assert out.get(f"{API}/institutional-models/{mv['id']}").status_code == 404
    # Usable for recognition jobs in its own project only.
    op = out.post(f"{API}/projects", json={"title": "Elsewhere"}).json()
    r = out.post(f"{API}/projects/{op['id']}/recognition-jobs", json={"page_ids": [ws["page"]["id"]], "recognition_model_version_id": mv["id"]})
    assert r.status_code in (404, 422)
    assert any(m["id"] == mv["id"] for m in pi.get(f"{API}/models?project_id={p['id']}&usable=true").json())
    ed = make_user("ed@uc.pt")
    pi.put(f"{API}/projects/{p['id']}/members", json={"email": ed.email, "role": "transcription_editor"})
    assert ed.post(f"{API}/projects/{p['id']}/institutional-models", data={"name": "x model", "version": "1"},
                   files={"file": ("m.mlmodel", src, "application/octet-stream")}).status_code == 403


def test_annotation_management_and_w3c_export(ws, make_user):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    l0 = t["lines"][0]["line_id"]
    a1 = pi.post(f"{API}/transcriptions/{t['id']}/annotations", json={"line_id": l0, "kind": "query", "body": "q̄ = que?"}).json()
    pi.post(f"{API}/transcriptions/{t['id']}/annotations", json={"kind": "comment", "body": "Página com manchas."})
    pi.post(f"{API}/annotations/{a1['id']}/resolve")
    lst = pi.get(f"{API}/projects/{p['id']}/annotations").json()
    assert lst["counts"] == {"open": 1, "resolved": 1, "by_kind": {"query": 1, "comment": 1}}
    assert pi.get(f"{API}/projects/{p['id']}/annotations?status=open").json()["items"][0]["kind"] == "comment"
    q = pi.get(f"{API}/projects/{p['id']}/annotations?kind=query").json()["items"][0]
    assert q["line_number"] == 1 and q["resolved_at"]
    w = pi.get(f"{API}/projects/{p['id']}/annotations/w3c")
    assert w.headers["content-type"].startswith("application/ld+json")
    coll = w.json()
    assert coll["@context"] == "http://www.w3.org/ns/anno.jsonld" and coll["type"] == "AnnotationCollection" and coll["total"] == 2
    items = {i["motivation"]: i for i in coll["first"]["items"]}
    sel = items["questioning"]["target"]["selector"]
    assert sel["type"] == "FragmentSelector" and sel["value"] == "xywh=50,60,900,50"
    assert items["questioning"]["body"] == {"type": "TextualBody", "value": "q̄ = que?", "format": "text/plain"}
    assert items["commenting"]["target"].endswith("/iiif/canvas/p1")
    ro = make_user("ro@uc.pt")
    pi.put(f"{API}/projects/{p['id']}/members", json={"email": ro.email, "role": "read_only"})
    assert ro.get(f"{API}/projects/{p['id']}/annotations").status_code == 200
    assert ro.get(f"{API}/projects/{p['id']}/annotations/w3c").status_code == 403


def test_iiif_manifest_is_valid_presentation_3_with_transcription(ws):  # noqa: F811
    from app.exports.validate import validate_iiif

    pi, t = ws["pi"], ws["t"]
    _review_all(pi, t, ["{q̄|que} a dita pessoa", "nam", "sabia"])
    r = pi.get(f"{API}/documents/{ws['page']['document_id']}/iiif/manifest.json")
    v = json.loads(r.headers["x-scriptorium-validation"])
    assert r.status_code == 200 and v["valid"] and v["schema"].startswith("IIIF Presentation 3.0") and v["errors"] == []
    m = r.json()
    assert validate_iiif(m)["valid"]
    assert m["@context"] == "http://iiif.io/api/presentation/3/context.json" and m["type"] == "Manifest"
    cv = m["items"][0]
    assert (cv["width"], cv["height"]) == (1000, 600)
    assert cv["items"][0]["items"][0]["motivation"] == "painting" and "/api/v1/assets/" in cv["items"][0]["items"][0]["body"]["id"]
    lines = cv["annotations"][0]["items"]
    assert [a["body"]["value"] for a in lines] == ["q̄ a dita pessoa", "nam", "sabia"]
    assert lines[0]["motivation"] == "supplementing" and lines[0]["target"].endswith("#xywh=50,60,900,50")
    htr = pi.get(f"{API}/documents/{ws['page']['document_id']}/iiif/manifest.json?layer=htr").json()
    assert htr["items"][0]["annotations"][0]["items"][1]["body"]["value"] == "dixe {que} nam"
    # The validator really rejects broken manifests (a canvas without dimensions, a non-URI id).
    broken = json.loads(json.dumps(m))
    del broken["items"][0]["width"]
    assert not validate_iiif(broken)["valid"]
    broken2 = json.loads(json.dumps(m))
    broken2["items"][0]["items"][0]["items"][0]["motivation"] = 42
    assert not validate_iiif(broken2)["valid"]


def test_analytics_from_stored_results(ws):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t, ["q̄ a dita pessoa", "dixe \\{que\\} nam", "sabia bem"])
    ds = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "A"}).json()
    gt = pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                 json={"transcription_id": t["id"], "split": "test"}).json()
    pi.post(f"{API}/evaluation/score", json={"gt_page_id": gt["id"], "recognition_id": str(ws["rec"].id), "save": True})
    a = pi.get(f"{API}/projects/{p['id']}/analytics").json()
    assert a["results"] == 1 and a["deletions"] and {d["char"] for d in a["deletions"]} >= {"b", "e", "m"}
    assert a["insertions"] == [] and a["confusions"] == []
    assert sum(b["lines"] for b in a["by_line_length"]) == 3
    # Fixture confidences are 0.9 for every line: one calibration bin, with the measured accuracy beside it.
    assert len(a["calibration"]) == 1 and a["calibration"][0]["mean_confidence"] == pytest.approx(0.9)
    acc = a["calibration"][0]["measured_char_accuracy"]
    assert acc == pytest.approx(1 - 4 / (16 + 14 + 9), abs=1e-6)
    assert a["expected_calibration_error"] == pytest.approx(abs(0.9 - acc))


def test_training_data_export_is_kraken_ready_page_xml(ws):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t, ["q̄ a dita pessoa", "dixe {illeg:3} nam", "sa{^bia^}"])
    ds = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "T"}).json()
    pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription", json={"transcription_id": t["id"], "split": "train"})
    r = pi.post(f"{API}/evaluation-datasets/{ds['id']}/training-data-export", json={})
    assert r.status_code == 201, r.text
    e = pi.get(f"{API}/exports/{r.json()['id']}").json()
    assert e["status"] == "completed", e
    assert e["summary"]["lines"] == {"train": 2, "validation": 0, "test": 0} and e["summary"]["excluded_lines"] == 1
    assert e["summary"]["validation"]["invalid"] == []
    z = zipfile.ZipFile(io.BytesIO(pi.get(f"{API}/exports/{e['id']}/download").content))
    names = z.namelist()
    assert "train.txt" in names and any(n.startswith("images/") for n in names)
    page_xml = next(n for n in names if n.startswith("train/") and n.endswith(".xml"))
    xml = z.read(page_xml).decode()
    assert 'imageFilename="../images/' in xml and "q̄ a dita pessoa" in xml and "sabia" in xml and "[…]" not in xml
    assert z.read("train.txt").decode().strip() == page_xml
