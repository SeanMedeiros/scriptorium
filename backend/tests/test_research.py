"""Phase 5 research tools: metrics, ground truth, leakage, evaluation, exports, provenance, reports.

Data sources are stated per test:
* metric and import tests use deterministic strings and the REAL ground-truth files of the TraPrInq
  validation set (Zenodo 13986218) and of HTRomance medieval-occitan (Zenodo 22656364);
* tests using the `ws` fixture run on a LABELLED FIXTURE recognition inserted in the database
  (not Kraken output) — they verify bookkeeping, not recognition quality;
* tests marked `kraken` run the real models (TraPrInq, CATMuS Medieval) — see test_research_real.py.
"""
import io
import json
import random
import unicodedata
import zipfile

import pytest
from lxml import etree
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.evaluation import gt_import, metrics
from app.exports import validate as val
from tests.conftest import REPO, SAMPLES
from tests.test_transcriptions import HTR, ws  # noqa: F401 — labelled fixture recognition

API = "/api/v1"
OCC_XML = REPO / "var/samples/occitan/btv1b9061575q_f6.xml"


# --------------------------------------------------------------------------------------- metrics
def test_levenshtein_matches_reference_implementation_on_random_strings():
    rnd = random.Random(7)
    alphabet = "aeiouq̄ũꝑ⁊ .,"
    for _ in range(300):
        a = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 25)))
        b = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 25)))
        assert metrics.char_distance(a, b) == metrics.reference_levenshtein(a, b)
        assert metrics.word_distance(a, b) == metrics.reference_levenshtein(a.split(), b.split())


def test_cer_wer_known_values_and_accuracy_definition():
    n = metrics.Normalization()
    sc = metrics.score_lines([("1", "kitten sat")], {"1": "sitting sat"}, n, reference_is_markup=False)
    assert (sc.char_errors, sc.ref_chars) == (3, 10) and sc.cer == pytest.approx(0.3)
    assert (sc.word_errors, sc.ref_words) == (1, 2) and sc.wer == pytest.approx(0.5)
    assert sc.summary()["char_accuracy"] == pytest.approx(0.7)
    # insertions can push the rate above 1: accuracy is then undefined, never negative
    big = metrics.score_lines([("1", "a")], {"1": "abcdef"}, n, reference_is_markup=False)
    assert big.cer == 5 and big.summary()["char_accuracy"] is None


def test_normalisation_is_explicit_and_configurable():
    nfc, nfd = "pessoã", unicodedata.normalize("NFD", "pessoã")
    assert nfc != nfd
    assert metrics.score_lines([("1", nfc)], {"1": nfd}, metrics.Normalization(), False).char_errors == 0
    raw = metrics.score_lines([("1", nfc)], {"1": nfd}, metrics.Normalization(unicode="none"), False)
    assert raw.char_errors == 2  # 'ã' vs 'a'+'◌̃': one substitution + one insertion
    assert metrics.score_lines([("1", "Dixe, que")], {"1": "dixe que"},
                               metrics.Normalization(casefold=True, remove_punctuation=True), False).char_errors == 0
    assert metrics.Normalization().describe()["version"] == metrics.METHODOLOGY_VERSION


def test_reference_markup_is_rendered_diplomatically_and_gap_lines_excluded():
    n = metrics.Normalization()
    sc = metrics.score_lines([("1", "{q̄|que} {?dita?} {+a+}pessoa"), ("2", "{illeg:4} nam"), ("3", "sabia")],
                             {"1": "q̄ dita pessoa", "2": "xxxx nam", "3": "sabia"}, n)
    assert sc.lines_excluded == 1 and sc.lines_matched == 2 and sc.char_errors == 0
    sc2 = metrics.score_lines([("1", "{illeg} nam")], {"1": "x nam"}, metrics.Normalization(exclude_gap_lines=False))
    assert sc2.lines_excluded == 0 and sc2.ref_chars == len("[…] nam")


def test_missing_hypothesis_lines_count_as_deletions_and_aggregate_is_micro_average():
    sc = metrics.score_lines([("1", "abcd"), ("2", "ef")], {"1": "abcd"}, metrics.Normalization(), False)
    assert sc.lines_missing == 1 and sc.char_errors == 2 and sc.ref_chars == 6
    agg = metrics.aggregate([{"ref_chars": 100, "char_errors": 10, "ref_words": 20, "word_errors": 4},
                             {"ref_chars": 10, "char_errors": 5, "ref_words": 2, "word_errors": 2}])
    assert agg["cer"] == pytest.approx(15 / 110) and agg["wer"] == pytest.approx(6 / 22)


# ------------------------------------------------------------------------- ground-truth import
@pytest.mark.parametrize("name,lines", [("PT-TT-TSO-IL-28-7926_m0052", 18), ("PT-TT-TSO-IL-28-687_m0019", 40),
                                        ("PT-TT-TSO-IL-28-12193_m0197", 10)])
def test_real_traprinq_alto_ground_truth_parses(name, lines):
    p = SAMPLES / f"{name}.xml"
    if not p.is_file():
        pytest.skip("TraPrInq validation files not available")
    g = gt_import.parse(p.read_bytes())
    assert g.format == "alto" and g.software == "Transkribus" and len(g.lines) == lines
    assert all(ln.baseline and len(ln.baseline) >= 2 for ln in g.lines)
    assert g.lines[0].text if name.endswith("7926_m0052") else True


def test_real_occitan_alto_with_polygons_parses_and_scales():
    if not OCC_XML.is_file():
        pytest.skip("HTRomance Occitan sample not available")
    g = gt_import.parse(OCC_XML.read_bytes())
    assert len(g.lines) == 112 and all(ln.boundary for ln in g.lines) and len(g.regions) == 9
    fit = gt_import.fit_to_image(g, 6883 // 2, 4859 // 2)  # proportionally resized image
    assert fit["scale"][0] == pytest.approx(0.5, abs=1e-3)
    assert max(x for ln in g.lines for x, _ in ln.boundary) < 6883 // 2
    with pytest.raises(gt_import.ImportError_) as e:
        gt_import.fit_to_image(gt_import.parse(OCC_XML.read_bytes()), 3000, 3000)
    assert e.value.code == "image_mismatch"


def test_import_rejects_non_ground_truth_xml():
    for data, code in ((b"<html/>", "unsupported_format"), (b"<alto", "invalid_xml")):
        with pytest.raises(gt_import.ImportError_) as e:
            gt_import.parse(data)
        assert e.value.code == code


# ----------------------------------------------------- ground truth from transcriptions (fixture)
def _review_all(api, t, texts=None):
    changes = []
    for i, ln in enumerate(t["lines"]):
        ch = {"line_id": ln["line_id"], "expected_version_id": ln["current"]["version_id"], "status": "reviewed"}
        if texts:
            ch["text"] = texts[i]
        changes.append(ch)
    r = api.post(f"{API}/transcriptions/{t['id']}/revisions", json={"changes": changes})
    assert r.status_code == 201, r.text
    return r.json()["transcription"]


def test_ground_truth_requires_fully_reviewed_transcription(ws):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    ds = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "Inquisição — teste"}).json()
    r = pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                json={"transcription_id": t["id"], "split": "test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ground_truth_not_reviewed"
    assert r.json()["error"]["details"]["count"] == 3
    _review_all(pi, t, ["{q̄|que} a dita pessoa", "dixe \\{que\\} nam", "sabia {?bem?}"])
    r = pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                json={"transcription_id": t["id"], "split": "test"})
    assert r.status_code == 201, r.text
    gt = r.json()
    assert gt["source"] == "transcription" and gt["line_count"] == 3 and gt["unicode_form"] == "invariant"
    assert "post-correction bias" in gt["note"]  # reference was corrected from this model's output
    assert gt["source_details"]["revision"] == 2


def test_score_against_ground_truth_never_compares_model_with_itself(ws):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t, ["{q̄|que} a dita pessoa", "dixe \\{que\\} nam", "sabia {?bem?}"])
    ds = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "D"}).json()
    gt = pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                 json={"transcription_id": t["id"], "split": "test"}).json()
    r = pi.post(f"{API}/evaluation/score", json={"gt_page_id": gt["id"], "recognition_id": str(ws["rec"].id), "save": True})
    assert r.status_code == 200, r.text
    s = r.json()
    # line 3: reference "sabia bem" vs hypothesis "sabia" → 4 deletions; lines 1–2 identical.
    assert s["alignment"] == "line" and s["char_errors"] == 4 and s["lines_matched"] == 3
    assert s["ref_chars"] == sum(len(unicodedata.normalize("NFC", x)) for x in ("q̄ a dita pessoa", "dixe {que} nam", "sabia bem"))
    assert s["cer"] == pytest.approx(s["char_errors"] / s["ref_chars"]) and s["stored"]
    res = pi.get(f"{API}/projects/{p['id']}/evaluation/results?group_by=model").json()
    assert res["groups"][0]["pages"] == 1 and res["groups"][0]["cer"] == pytest.approx(s["cer"])
    by_lang = pi.get(f"{API}/projects/{p['id']}/evaluation/results?group_by=language").json()
    assert by_lang["groups"][0]["key"] == "(not recorded)"
    assert pi.get(f"{API}/projects/{p['id']}/evaluation/results?group_by=colour").status_code == 422


def test_data_leakage_between_splits_is_refused_including_duplicate_images(ws, db):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t)
    a = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "A"}).json()
    b = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "B"}).json()
    assert pi.post(f"{API}/evaluation-datasets/{a['id']}/ground-truth/from-transcription",
                   json={"transcription_id": t["id"], "split": "train"}).status_code == 201
    r = pi.post(f"{API}/evaluation-datasets/{a['id']}/ground-truth/from-transcription",
                json={"transcription_id": t["id"], "split": "train"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_ground_truth"
    r = pi.post(f"{API}/evaluation-datasets/{b['id']}/ground-truth/from-transcription",
                json={"transcription_id": t["id"], "split": "test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "data_leakage"
    # The same image uploaded again as another document is detected by its checksum.
    page_png = pi.get(f"{API}/documents/{ws['page']['document_id']}/original")
    c = pi.get(f"{API}/projects/{p['id']}/collections").json()[0]
    d2 = pi.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
                 files=[("files", ("copy.png", page_png.content, "image/png"))]).json()[0]
    page2 = pi.get(f"{API}/documents/{d2['id']}").json()["pages"][0]
    alto = _fixture_alto(1000, 600)
    r = pi.post(f"{API}/evaluation-datasets/{b['id']}/ground-truth/import",
                data={"page_id": page2["id"], "split": "test"}, files={"file": ("gt.xml", alto, "application/xml")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "data_leakage"
    assert r.json()["error"]["details"]["same_page"] is False
    # Training split is never evaluated.
    assert pi.post(f"{API}/evaluation-datasets/{a['id']}/runs",
                   json={"model_version_ids": [str(ws["rec"].model_version_id)], "split": "train"}).status_code == 422


def _fixture_alto(w, h) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Layout><Page WIDTH="{w}" HEIGHT="{h}"><PrintSpace>
<TextBlock ID="b1"><TextLine ID="t1" BASELINE="50,100 950,100"><String CONTENT="q̄ a dita"/><SP/><String CONTENT="pessoa"/></TextLine>
</TextBlock></PrintSpace></Page></Layout></alto>""".encode()


def test_ground_truth_rows_are_immutable_and_withdrawal_is_recorded(ws, db):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t)
    ds = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "I"}).json()
    gt = pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                 json={"transcription_id": t["id"], "split": "validation"}).json()
    for stmt in ("UPDATE ground_truth_lines SET text = 'x'", "UPDATE ground_truth_pages SET split = 'train'"):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(stmt))
        db.rollback()
    r = pi.post(f"{API}/ground-truth/{gt['id']}/withdraw", json={"reason": "wrong page"})
    assert r.status_code == 200 and r.json()["withdrawn"]
    d = pi.get(f"{API}/evaluation-datasets/{ds['id']}").json()
    assert d["pages"] == 0 and d["ground_truth"][0]["withdrawn"]
    # After withdrawal the page may be used in another split.
    assert pi.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/from-transcription",
                   json={"transcription_id": t["id"], "split": "test"}).json()["error"]["code"] == "duplicate_ground_truth"
    ds2 = pi.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "I2"}).json()
    assert pi.post(f"{API}/evaluation-datasets/{ds2['id']}/ground-truth/from-transcription",
                   json={"transcription_id": t["id"], "split": "test"}).status_code == 201


def test_ground_truth_curation_requires_review_permission(ws, make_user):  # noqa: F811
    ed = make_user("editor@uc.pt", "Ed")
    ws["pi"].put(f"{API}/projects/{ws['project']['id']}/members", json={"email": ed.email, "role": "transcription_editor"})
    assert ed.post(f"{API}/projects/{ws['project']['id']}/evaluation-datasets", json={"name": "X"}).status_code == 403
    assert ed.get(f"{API}/projects/{ws['project']['id']}/evaluation-datasets").status_code == 200


# ----------------------------------------------------------------------------- exports (fixture)
XML_FORMATS = ("alto", "page", "tei", "hocr")


@pytest.mark.parametrize("fmt", ["txt", "json", "alto", "page", "hocr", "tei", "docx", "pdf"])
def test_every_format_exports_and_xml_validates_against_official_schemas(ws, fmt):  # noqa: F811
    pi, t = ws["pi"], ws["t"]
    _review_all(pi, t, ["{q̄|que} a {?dita?} pessoa", "dixe {-que-} {illeg:3} nam", "sa{^bia^} {+e+}"])
    r = pi.get(f"{API}/transcriptions/{t['id']}/export?format={fmt}&layer=corrected")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    data = r.content
    if fmt in XML_FORMATS:
        v = json.loads(r.headers["x-scriptorium-validation"])
        assert v["valid"], v
        assert val.validate(fmt, data)["valid"]
    if fmt == "txt":
        body = data.decode()
        assert "q̄ a dita pessoa" in body and "dixe que […] nam" in body and "transcription r2" in body
    if fmt == "json":
        j = json.loads(data)
        ln = j["pages"][0]["lines"][0]
        assert ln["htr"] == HTR[0] and ln["markup"] == "{q̄|que} a {?dita?} pessoa" and ln["bbox"]
        assert j["pages"][0]["recognition"]["model"]["slug"] == "fixture-model"
        assert j["pages"][0]["quality"]["reviewed"] == 3 and j["pages"][0]["quality"]["reviewed_text_share"] == 1
    if fmt == "tei":
        doc = etree.fromstring(data)
        ns = {"t": "http://www.tei-c.org/ns/1.0"}
        assert doc.find(".//t:choice/t:abbr", ns).text == "q̄" and doc.find(".//t:choice/t:expan", ns).text == "que"
        assert doc.find(".//t:unclear", ns).text == "dita"
        assert doc.find(".//t:gap", ns).get("quantity") == "3" and doc.find(".//t:del", ns).text == "que"
        assert doc.find(".//t:hi", ns).get("rend") == "superscript" and doc.find(".//t:supplied", ns).text == "e"
        assert len(doc.findall(".//t:zone", ns)) == 3 and doc.find(".//t:lb", ns).get("facs").startswith("#z_")
    if fmt == "page":
        doc = etree.fromstring(data)
        ns = {"p": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"}
        line = doc.find(".//p:TextLine", ns)
        eq = line.findall("p:TextEquiv", ns)
        assert [e.get("index") for e in eq] == ["1", "2"]
        assert eq[0].find("p:Unicode", ns).text == "q̄ a dita pessoa" and eq[1].find("p:Unicode", ns).text == HTR[0]
        assert line.find("p:Coords", ns).get("points").startswith("50,60")
    if fmt == "alto":
        doc = etree.fromstring(data)
        ns = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}
        assert doc.find(".//a:TextLine", ns).get("BASELINE") == "50,100 950,100"
        assert [p.find("a:processingCategory", ns).text for p in doc.findall(".//a:Processing", ns)] == \
            ["contentGeneration", "contentModification"]
    if fmt == "docx":
        z = zipfile.ZipFile(io.BytesIO(data))
        assert "q̄ a dita pessoa" in z.read("word/document.xml").decode()
    if fmt == "pdf":
        assert data.startswith(b"%PDF") and len(data) > 3000


def test_tei_mapping_is_configurable_and_htr_layer_exports_raw_output(ws):  # noqa: F811
    pi, t = ws["pi"], ws["t"]
    _review_all(pi, t, ["{q̄|que} a {?dita?} pessoa", "nam", "sabia"])
    ns = {"t": "http://www.tei-c.org/ns/1.0"}
    r = pi.get(f"{API}/transcriptions/{t['id']}/export?format=tei&tei_abbreviations=expan&tei_cert=true&tei_facsimile=false")
    doc = etree.fromstring(r.content)
    assert json.loads(r.headers["x-scriptorium-validation"])["valid"]
    assert doc.find(".//t:choice", ns) is None and doc.find(".//t:expan", ns).text == "que"
    assert doc.find(".//t:unclear", ns).get("cert") == "low" and doc.find(".//t:facsimile", ns) is None
    r = pi.get(f"{API}/transcriptions/{t['id']}/export?format=txt&layer=htr")
    assert HTR[1] in r.content.decode() and "transcription r2, layer htr" in r.content.decode()
    r = pi.get(f"{API}/transcriptions/{t['id']}/export?format=txt&layer=expanded")
    assert "que a dita pessoa" in r.content.decode()
    assert pi.get(f"{API}/transcriptions/{t['id']}/export?format=alto&layer=markup").status_code == 422
    assert pi.get(f"{API}/transcriptions/{t['id']}/export?format=tei&layer=reading").status_code == 422
    # export of a past revision
    r = pi.get(f"{API}/transcriptions/{t['id']}/export?format=txt&at_revision=1")
    assert "transcription r1" in r.content.decode() and HTR[0] in r.content.decode()


def test_batch_export_zip_has_manifest_checksums_and_validation(ws, db):  # noqa: F811
    import hashlib

    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t)
    r = pi.post(f"{API}/projects/{p['id']}/exports", json={"scope": "document", "scope_id": ws["page"]["document_id"],
                                                          "formats": ["tei", "alto", "page", "hocr", "txt", "pdf"]})
    assert r.status_code == 201, r.text
    e = pi.get(f"{API}/exports/{r.json()['id']}").json()
    assert e["status"] == "completed", e
    assert e["summary"]["validation"]["invalid"] == [] and e["summary"]["validation"]["checked"] == 4
    z = pi.get(f"{API}/exports/{e['id']}/download")
    assert z.status_code == 200 and hashlib.sha256(z.content).hexdigest() == e["sha256"]
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    man = json.loads(zf.read("manifest.json"))
    for f in man["files"]:
        assert hashlib.sha256(zf.read(f["path"])).hexdigest() == f["sha256"]
    assert any(f["path"].endswith("/alto/p0001.alto.xml") for f in man["files"])
    assert man["pages"][0]["text_source"] == "transcription r2"
    # collection scope; another project's collection is invisible
    c = pi.get(f"{API}/projects/{p['id']}/collections").json()[0]
    assert pi.post(f"{API}/projects/{p['id']}/exports", json={"scope": "collection", "scope_id": c["id"],
                                                             "formats": ["json"]}).status_code == 201


def test_provenance_and_report_are_complete_and_permissioned(ws, make_user):  # noqa: F811
    pi, t, p = ws["pi"], ws["t"], ws["project"]
    _review_all(pi, t)
    pv = pi.get(f"{API}/transcriptions/{t['id']}/provenance").json()
    assert pv["page"]["image"]["sha256"] and pv["recognition"]["model"]["slug"] == "fixture-model"
    assert pv["transcription"]["revision"] == 2 and pv["transcription"]["contributors"] == ["PI"]
    assert pv["segmentation"]["source"] == "kraken" and pv["quality"]["reviewed"] == 3
    rep = pi.get(f"{API}/projects/{p['id']}/report").json()
    assert rep["counts"]["pages"] == 1 and rep["counts"]["revisions"] == 1 and rep["lines"]["reviewed"] == 3
    assert rep["contributors"][0]["name"] == "PI" and rep["contributors"][0]["lines_changed"] == 3
    csv = pi.get(f"{API}/projects/{p['id']}/report?format=csv")
    assert csv.headers["content-type"].startswith("text/csv") and "contributors,PI" in csv.text
    ro = make_user("reader@uc.pt", "Reader")
    pi.put(f"{API}/projects/{p['id']}/members", json={"email": ro.email, "role": "read_only"})
    assert ro.get(f"{API}/transcriptions/{t['id']}/provenance").status_code == 200
    assert ro.get(f"{API}/transcriptions/{t['id']}/export?format=txt").status_code == 403
    outsider = make_user("out@ox.ac.uk")
    assert outsider.get(f"{API}/transcriptions/{t['id']}/export?format=txt").status_code == 404
    assert outsider.get(f"{API}/projects/{p['id']}/report").status_code == 404


def test_hocr_structural_check_detects_real_problems():
    bad = b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><div class="ocr_page" id="p" title="bbox 0 0 10 10"><span class="ocr_line" id="l" title="bbox 0 0 20 5">x</span></div></body></html>'
    r = val.validate("hocr", bad)
    assert not r["valid"] and "no formal schema" in r["method"]
    assert any("ocr-system" in e for e in r["errors"]) and any("outside" in e for e in r["errors"])
