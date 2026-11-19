"""Transcription editing, revisions, concurrency, history, restore, review, annotations.

Data: the page, segmentation and recognition rows below are a LABELLED FIXTURE inserted
directly into the database (not produced by Kraken). These tests verify the editorial
versioning machinery; real HTR → editing is covered by the E2E suite.
"""
import io
import unicodedata
import uuid

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import (
    LineRecognition,
    ModelSource,
    ModelStatus,
    ModelTask,
    ModelVersion,
    Recognition,
    RecognitionModel,
    Segmentation,
    TextLine,
)

pytestmark = pytest.mark.integration
API = "/api/v1"
HTR = ["q̄ a dita pessoa", "dixe {que} nam", "sabia"]  # fixture texts (one contains literal braces)


@pytest.fixture
def ws(make_user, db):
    pi = make_user("pi@uc.pt", "PI")
    p = pi.post(f"{API}/projects", json={"title": "Edição"}).json()
    c = pi.post(f"{API}/projects/{p['id']}/collections", json={"title": "C"}).json()
    b = io.BytesIO()
    Image.new("L", (1000, 600), 220).save(b, format="PNG")
    d = pi.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents", files=[("files", ("f.png", b.getvalue(), "image/png"))]).json()[0]
    page = pi.get(f"{API}/documents/{d['id']}").json()["pages"][0]
    m = RecognitionModel(slug="fixture-model", name="Fixture model", task=ModelTask.RECOGNITION, source=ModelSource.MANUAL)
    db.add(m)
    db.flush()
    mv = ModelVersion(model_id=m.id, version="0", file_name="none", status=ModelStatus.VALIDATED, unicode_normalization="NFD")
    db.add(mv)
    db.flush()
    seg = Segmentation(id=uuid.uuid4(), page_id=page["id"], source="kraken", input_asset_id=page["image_asset_id"])
    db.add(seg)
    db.flush()
    lines = []
    for i in range(3):
        ln = TextLine(id=uuid.uuid4(), segmentation_id=seg.id, order_index=i, baseline=[[50, 100 + 150 * i], [950, 100 + 150 * i]],
                      boundary=[[50, 60 + 150 * i], [950, 60 + 150 * i], [950, 110 + 150 * i], [50, 110 + 150 * i]],
                      bbox=[50, 60 + 150 * i, 950, 110 + 150 * i])
        db.add(ln)
        lines.append(ln)
    db.flush()
    rec = Recognition(id=uuid.uuid4(), segmentation_id=seg.id, page_id=page["id"], model_version_id=mv.id,
                      input_asset_id=page["image_asset_id"], line_count=3, char_count=sum(map(len, HTR)))
    db.add(rec)
    db.flush()
    for i, ln in enumerate(lines):
        db.add(LineRecognition(recognition_id=rec.id, line_id=ln.id, order_index=i, text=HTR[i], confidences=[0.9] * len(HTR[i]),
                               mean_confidence=0.9))
    db.commit()
    t = pi.post(f"{API}/pages/{page['id']}/transcriptions", json={"recognition_id": str(rec.id)})
    assert t.status_code == 201, t.text
    return {"pi": pi, "project": p, "page": page, "t": t.json(), "rec": rec, "seg": seg, "lines": lines}


def _line(t, i):
    return t["lines"][i]


def test_initial_revision_copies_htr_verbatim_and_policy_follows_model(ws):
    t = ws["t"]
    assert t["head_revision"] == 1 and t["model"]["unicode_normalization"] == "NFD"
    assert t["policy"]["unicode_input"] == "NFD"
    for i, ln in enumerate(t["lines"]):
        assert ln["original"]["text"] == HTR[i]
        assert ln["renderings"]["diplomatic"] == HTR[i]  # literal braces survive (escaped)
        assert ln["current"]["status"] == "unreviewed" and ln["current"]["source"] == "htr"
    assert t["counts"]["unreviewed"] == 3


def test_creation_is_idempotent(ws):
    again = ws["pi"].post(f"{API}/pages/{ws['page']['id']}/transcriptions", json={"recognition_id": str(ws["rec"].id)}).json()
    assert again["id"] == ws["t"]["id"] and again["head_revision"] == 1


def test_edit_creates_revision_keeps_original_and_applies_unicode_policy(ws, db):
    pi, t = ws["pi"], ws["t"]
    l0 = _line(t, 0)
    nfc = unicodedata.normalize("NFC", "{q̄|que} a {?dita?} pessõa")
    r = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"message": "first pass", "changes": [
        {"line_id": l0["line_id"], "expected_version_id": l0["current"]["version_id"], "text": nfc,
         "normalized_text": "que a dita pessoa", "note": "abbreviation mark over q"}]})
    assert r.status_code == 400 or r.status_code == 201, r.text
    # TraPrInq-like NFD policy, default abbreviations policy = mark-expansions → accepted
    assert r.status_code == 201
    out = r.json()["transcription"]
    cur = out["lines"][0]["current"]
    assert out["head_revision"] == 2 and cur["status"] == "corrected" and cur["has_uncertain"] is True
    assert cur["text"] == unicodedata.normalize("NFD", nfc)  # explicit policy: human input stored as NFD
    assert out["lines"][0]["original"]["text"] == HTR[0]  # original HTR untouched
    assert out["lines"][0]["renderings"]["expanded"].startswith("que a dita")
    assert cur["normalized_text"] == unicodedata.normalize("NFD", "que a dita pessoa") and cur["note"]
    # history tables are append-only
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE line_versions SET text = 'x'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE line_recognitions SET text = 'x'"))
    db.rollback()


def test_concurrent_edit_is_detected(ws, make_user):
    pi, t = ws["pi"], ws["t"]
    ed = make_user("editor@uc.pt", "Editor")
    pi.put(f"{API}/projects/{ws['project']['id']}/members", json={"email": ed.email, "role": "transcription_editor"})
    l1 = _line(t, 1)
    v0 = l1["current"]["version_id"]
    a = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l1["line_id"], "expected_version_id": v0, "text": "dixe que nam"}]})
    assert a.status_code == 201
    b = ed.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l1["line_id"], "expected_version_id": v0, "text": "disse que não"}]})
    assert b.status_code == 409 and b.json()["error"]["code"] == "edit_conflict"
    conflict = b.json()["error"]["details"]["conflicts"][0]
    assert conflict["current_text"] == "dixe que nam"
    # Nothing was lost: after reloading, the second editor can save on top.
    cur = ed.get(f"{API}/transcriptions/{t['id']}").json()["lines"][1]["current"]["version_id"]
    assert ed.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l1["line_id"], "expected_version_id": cur, "text": "disse que não"}]}).status_code == 201


def test_invalid_markup_and_policy_violations_are_rejected(ws):
    pi, t = ws["pi"], ws["t"]
    l2 = _line(t, 2)
    r = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": l2["current"]["version_id"], "text": "{?sabia"}]})
    assert r.status_code == 422 and "unclosed" in r.json()["error"]["details"]["errors"][0]["error"]
    pol = pi.put(f"{API}/transcriptions/{t['id']}/policy", json={"type": "graphematic", "abbreviations": "preserve", "unicode_input": "NFD"})
    assert pol.status_code == 200
    r = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": l2["current"]["version_id"], "text": "{sab.|sabia}"}]})
    assert r.status_code == 422 and "preserves abbreviations" in r.json()["error"]["details"]["errors"][0]["error"]
    r = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": l2["current"]["version_id"], "text": "a\nb"}]})
    assert r.status_code == 422


def test_history_point_in_time_and_restore(ws):
    pi, t = ws["pi"], ws["t"]
    l0 = _line(t, 0)
    v = l0["current"]["version_id"]
    for txt in ("versão um", "versão dois"):
        res = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l0["line_id"], "expected_version_id": v, "text": txt}]}).json()
        v = res["transcription"]["lines"][0]["current"]["version_id"]
    hist = pi.get(f"{API}/transcriptions/{t['id']}/lines/{l0['line_id']}/history").json()
    assert [h["revision"] for h in hist] == [3, 2, 1]
    assert [unicodedata.normalize("NFC", h["text"]) for h in hist] == ["versão dois", "versão um", HTR[0]]
    past = pi.get(f"{API}/transcriptions/{t['id']}", params={"at_revision": 2}).json()
    assert unicodedata.normalize("NFC", past["lines"][0]["current"]["text"]) == "versão um" and past["viewing_revision"] == 2
    r = pi.post(f"{API}/transcriptions/{t['id']}/lines/{l0['line_id']}/restore", json={"version_id": hist[2]["version_id"]})
    assert r.status_code == 201 and r.json()["revision"]["kind"] == "restore"
    now = r.json()["transcription"]["lines"][0]["current"]
    assert now["text"] == HTR[0] and now["source"] == "restore"
    revs = pi.get(f"{API}/transcriptions/{t['id']}/revisions").json()
    assert [x["kind"] for x in revs] == ["restore", "edit", "edit", "initial"] and revs[0]["author"] == "PI"


def test_review_requires_reviewer_permission_and_is_reset_by_edits(ws, make_user):
    pi, t = ws["pi"], ws["t"]
    ed = make_user("ed@uc.pt", "Ed")
    rv = make_user("rev@uc.pt", "Rev")
    pi.put(f"{API}/projects/{ws['project']['id']}/members", json={"email": ed.email, "role": "transcription_editor"})
    pi.put(f"{API}/projects/{ws['project']['id']}/members", json={"email": rv.email, "role": "reviewer"})
    l2 = _line(t, 2)
    r = ed.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": l2["current"]["version_id"], "status": "reviewed"}]})
    assert r.status_code == 422 and "reviewer" in r.json()["error"]["details"]["errors"][0]["error"]
    r = rv.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": l2["current"]["version_id"], "status": "reviewed"}]})
    assert r.status_code == 201 and r.json()["revision"]["kind"] == "review"
    cur = r.json()["transcription"]["lines"][2]["current"]
    # Reviewer cannot edit text.
    assert rv.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": cur["version_id"], "text": "x"}]}).status_code == 403
    # An edit after review requires a new review.
    e = ed.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l2["line_id"], "expected_version_id": cur["version_id"], "text": "sabiam"}]})
    assert e.json()["transcription"]["lines"][2]["current"]["status"] == "corrected"


def test_read_only_and_outsiders(ws, make_user):
    pi, t = ws["pi"], ws["t"]
    ro = make_user("ro@uc.pt")
    pi.put(f"{API}/projects/{ws['project']['id']}/members", json={"email": ro.email, "role": "read_only"})
    assert ro.get(f"{API}/transcriptions/{t['id']}").status_code == 200
    l0 = _line(t, 0)
    assert ro.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l0["line_id"], "expected_version_id": l0["current"]["version_id"], "text": "x"}]}).status_code == 403
    out = make_user("out@uc.pt")
    assert out.get(f"{API}/transcriptions/{t['id']}").status_code == 404


def test_no_op_save_creates_no_revision(ws):
    pi, t = ws["pi"], ws["t"]
    l0 = _line(t, 0)
    r = pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l0["line_id"], "expected_version_id": l0["current"]["version_id"], "text": l0["current"]["text"]}]})
    assert r.status_code == 201 and r.json()["revision"] is None
    assert pi.get(f"{API}/transcriptions/{t['id']}").json()["head_revision"] == 1


def test_annotations(ws):
    pi, t = ws["pi"], ws["t"]
    l1 = _line(t, 1)
    a = pi.post(f"{API}/transcriptions/{t['id']}/annotations", json={"line_id": l1["line_id"], "kind": "query", "body": "Ler 'nam' ou 'não'?"})
    assert a.status_code == 201
    assert pi.get(f"{API}/transcriptions/{t['id']}/annotations").json()[0]["body"].startswith("Ler")
    assert pi.post(f"{API}/annotations/{a.json()['id']}/resolve").json()["resolved_at"]


def test_manual_segmentation_is_a_new_segmentation(ws, db):
    pi = ws["pi"]
    seg = ws["seg"]
    r = pi.post(f"{API}/pages/{ws['page']['id']}/segmentations", json={
        "parent_segmentation_id": str(seg.id), "message": "merged and redrawn",
        "lines": [{"baseline": [[50, 100], [950, 100]], "boundary": [[50, 60], [950, 60], [950, 110], [50, 110]],
                   "source_line_id": str(ws["lines"][0].id)}, {"baseline": [[60, 400], [900, 405]]}]})
    assert r.status_code == 201 and r.json()["lines_without_boundary"] == 1
    new = db.get(Segmentation, uuid.UUID(r.json()["id"]))
    assert new.source == "manual" and new.parent_segmentation_id == seg.id
    assert db.scalar(text("SELECT count(*) FROM text_lines WHERE segmentation_id = :s"), {"s": seg.id}) == 3  # parent intact
    bad = pi.post(f"{API}/pages/{ws['page']['id']}/segmentations", json={"parent_segmentation_id": str(seg.id), "lines": [{"baseline": [[0, 0], [5000, 0]]}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "out_of_bounds"


def test_project_policy_applies_to_new_transcriptions(ws, db):
    pi = ws["pi"]
    r = pi.put(f"{API}/projects/{ws['project']['id']}/transcription-policy", json={"type": "diplomatic", "unicode_input": "NFC", "abbreviations": "preserve"})
    assert r.status_code == 200 and r.json()["unicode_input"] == "NFC"
    v = pi.post(f"{API}/editorial/validate", json={"text": "{q̄|que}"}).json()
    assert v["valid"] and v["renderings"]["expanded"] == "que"


def test_dashboard_counts_current_line_status(ws):
    pi, t = ws["pi"], ws["t"]
    l0 = _line(t, 0)
    pi.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": [{"line_id": l0["line_id"], "expected_version_id": l0["current"]["version_id"], "text": "corrigida"}]})
    d = pi.get(f"{API}/dashboard").json()
    assert d["line_status"] == {"unreviewed": 2, "corrected": 1, "reviewed": 0}  # latest version per line only
