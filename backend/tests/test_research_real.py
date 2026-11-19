"""REAL evaluation and model comparison (Phase 5). Nothing is mocked.

Ground truth: the Transkribus ALTO of Torre do Tombo PT/TT/TSO-IL/28/7926 image m0052 from the TraPrInq
"Validation_Set_FINAL" (Zenodo 13986218, CC-BY-4.0). The model record does not state whether these pages
were excluded from training, so results are a functional check, not an independent benchmark.
Models: TraPrInq V1 (Zenodo 14543904) and CATMuS Medieval 1.5.0 (Zenodo 12743230), run by Kraken 7.1.1.
"""
import json

import pytest

from app.exports import validate as val
from tests.conftest import sample_page

pytestmark = [pytest.mark.integration, pytest.mark.kraken]
API = "/api/v1"
NAME = "PT-TT-TSO-IL-28-7926_m0052"


def _setup(make_user):
    path = sample_page(NAME)
    u = make_user("avaliacao@uc.pt", "Avaliadora")
    p = u.post(f"{API}/projects", json={"title": "Avaliação HTR", "languages": ["por"]}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Validação TraPrInq"}).json()
    r = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
               files=[("files", (path.name, path.read_bytes(), "image/jpeg"))],
               data={"metadata": json.dumps({"languages": ["por"], "archival_reference": "PT/TT/TSO-IL/28/7926",
                                             "script_type": "manuscript"})})
    doc = u.get(f"{API}/documents/{r.json()[0]['id']}").json()
    ds = u.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "TraPrInq validation sample"}).json()
    gt = u.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/import",
                data={"page_id": doc["pages"][0]["id"], "split": "test",
                      "note": "TraPrInq Validation_Set_FINAL (Zenodo 13986218); overlap with training data not documented"},
                files={"file": (f"{NAME}.xml", path.with_suffix(".xml").read_bytes(), "application/xml")})
    assert gt.status_code == 201, gt.text
    return u, p, doc, ds, gt.json()


def test_real_evaluation_run_line_aligned_on_ground_truth_segmentation(make_user, traprinq, blla):
    u, p, doc, ds, gt = _setup(make_user)
    assert gt["source"] == "alto" and gt["line_count"] == 18 and gt["source_details"]["declared_software"] == "Transkribus"
    runs = u.post(f"{API}/evaluation-datasets/{ds['id']}/runs", json={"model_version_ids": [str(traprinq.id)], "split": "test"})
    assert runs.status_code == 201, runs.text
    run = u.get(f"{API}/evaluation-runs/{runs.json()[0]['id']}").json()
    assert run["status"] == "completed" and run["scored_pages"] == 1, run
    res = run["results"][0]
    # Recognition ran on the ground-truth baselines (Kraken computed the polygons), so every
    # reference line has its own hypothesis: line-aligned CER, as with `ketos test`.
    assert res["alignment"] == "line" and res["lines_matched"] == 18 and res["lines_missing"] == 0
    assert res["methodology"]["version"] == "scriptorium-metrics/1" and res["methodology"]["unicode"] == "NFC"
    assert 0 < res["cer"] < 0.5 and res["wer"] >= res["cer"]
    print(f"\n[real] TraPrInq V1 on {NAME} (18 lines, GT segmentation): CER {res['cer']:.4f}, WER {res['wer']:.4f} "
          f"({res['char_errors']}/{res['ref_chars']} chars, {res['word_errors']}/{res['ref_words']} words)")
    detail = u.get(f"{API}/evaluation-results/{res['result_id']}").json()
    assert len(detail["details"]["lines"]) == 18 and all(ln["hyp"] is not None for ln in detail["details"]["lines"])
    grouped = u.get(f"{API}/projects/{p['id']}/evaluation/results?group_by=language").json()
    assert grouped["groups"][0]["key"] == "por" and grouped["groups"][0]["cer"] == pytest.approx(res["cer"])


def test_real_comparison_of_two_models_and_combined_transcription(make_user, traprinq, catmus, blla):
    u, p, doc, ds, gt = _setup(make_user)
    page_id = doc["pages"][0]["id"]
    r = u.post(f"{API}/pages/{page_id}/comparisons", json={"segmentation_id": gt["segmentation_id"],
                                                            "model_version_ids": [str(traprinq.id), str(catmus.id)]})
    assert r.status_code == 201, r.text
    cmp_ = u.get(f"{API}/comparisons/{r.json()['id']}").json()
    assert [m["job_status"] for m in cmp_["models"]] == ["completed", "completed"], cmp_["models"]
    for m in cmp_["models"]:
        assert m["duration_ms"] > 0 and m["kraken"] == "7.1.1" and m["line_count"] == 18
        assert m["score"]["alignment"] == "line" and m["score"]["ref_chars"] > 300
    tp, cm = cmp_["models"]
    print(f"\n[real] comparison on {NAME}: TraPrInq CER {tp['score']['cer']:.4f} ({tp['duration_ms']} ms) · "
          f"CATMuS 1.5.0 CER {cm['score']['cer']:.4f} ({cm['duration_ms']} ms)")
    assert len(cmp_["lines"]) == 18 and all(len(ln["outputs"]) == 2 for ln in cmp_["lines"])
    assert cmp_["pairwise"][0]["char_distance"] > 0 and cmp_["ground_truth"]["aligned"]
    # Combine: line 1 from CATMuS, the rest from TraPrInq; every line keeps its origin.
    first = cmp_["lines"][0]["key"]
    c = u.post(f"{API}/comparisons/{cmp_['id']}/combine",
               json={"primary_recognition_id": tp["recognition_id"], "selections": {first: cm["recognition_id"]}})
    assert c.status_code == 201, c.text
    assert c.json()["lines_by_model"] == {"traprinq V1": 17, "catmus-medieval 1.5.0": 1}
    t = u.get(f"{API}/transcriptions/{c.json()['transcription_id']}").json()
    assert t["origin"] == "combined" and t["comparison_id"] == cmp_["id"]
    assert t["lines"][0]["original"]["model"] == "CATMuS Medieval · 1.5.0"
    assert t["lines"][0]["current"]["text"] == cmp_["lines"][0]["outputs"][cm["model_version_id"]]["text"].replace("{", "\\{").replace("}", "\\}").replace("|", "\\|")
    assert t["lines"][1]["original"]["model"] == "TraPrInq · V1"
    pv = u.get(f"{API}/transcriptions/{t['id']}/provenance").json()
    assert pv["combined_from"] == ["catmus-medieval 1.5.0", "traprinq V1"]
    for fmt in ("page", "alto", "tei"):
        e = u.get(f"{API}/transcriptions/{t['id']}/export?format={fmt}&layer=corrected")
        assert e.status_code == 200 and json.loads(e.headers["x-scriptorium-validation"])["valid"], e.headers.get("x-scriptorium-validation")
        assert val.validate(fmt, e.content)["valid"]
