"""REAL Zenodo integration tests: live metadata, real downloads (CATMuS Medieval, ~16 MB per
version), checksum verification against the record, real Kraken loading, version
management. Requires network access to zenodo.org; skipped with an explicit reason otherwise."""
import httpx
import pytest
from sqlalchemy import select

from app.models import ModelVersion, RecognitionModel

pytestmark = [pytest.mark.integration, pytest.mark.zenodo, pytest.mark.kraken]
API = "/api/v1"


@pytest.fixture(scope="module", autouse=True)
def _zenodo_reachable():
    try:
        httpx.get("https://zenodo.org/api/records/14543904", timeout=15).raise_for_status()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"zenodo.org not reachable from this environment: {e}")


@pytest.fixture
def admin(make_user):
    return make_user("registry-admin@archive.org")  # first account → platform administrator


def test_preview_traprinq_shows_real_metadata_without_installing(admin, db):
    r = admin.get(f"{API}/admin/model-registry/preview", params={"identifier": "10.5281/zenodo.14543904"})
    assert r.status_code == 200, r.text
    p = r.json()
    rec = p["record"]
    assert rec["record_id"] == "14543904" and rec["license_id"] == "cc-by-4.0"
    assert [c["name"] for c in rec["creators"]] == ["Weslley Oliveira"]
    assert p["model_files"] == [{"key": "traprinq.mlmodel", "size": 159961943, "checksum": "md5:b7f84ede75a0665c275346d94fdc1b95"}]
    assert p["model_card"]["format"] == "htrmopo-v0" and p["model_card"]["valid"] is True
    assert p["source_reported_accuracy"] == 94.87  # as published by the authors, labelled as such in the UI
    assert p["unicode_normalization"]["form"] == "NFD" and "Normalization" in p["unicode_normalization"]["quote"]
    assert p["compatibility"]["verdict"] == "likely"
    assert "never redistributes" in p["license"]["notice"]
    assert db.scalar(select(ModelVersion.id)) is None  # preview writes nothing


def test_catmus_concept_doi_resolves_to_latest_and_detects_identical_binaries(admin):
    p = admin.get(f"{API}/admin/model-registry/preview", params={"identifier": "10.5281/zenodo.10066218"}).json()
    assert p["is_latest"] is True
    versions = {v["version"]: v for v in p["versions"]}
    assert {"1.0.0", "1.5.0", "1.6.0"} <= set(versions)
    latest = next(v for v in p["versions"] if v["is_latest"])
    assert p["record"]["version"] == latest["version"]
    # 1.6.2 republished the 1.6.0 binary (same checksum) with a new model card.
    if "1.6.2" in versions:
        assert "1.6.0" in versions["1.6.2"]["identical_binary_as"]
        assert p["model_card"]["format"] == "htrmopo-v1" and "lat" in p["model_card"]["card"]["language"]
    assert versions["1.5.0"]["identical_binary_as"] == []


def test_install_requires_licence_acceptance(admin):
    r = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "12743230", "accept_license": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "license_not_accepted"


def test_non_admin_cannot_use_registry(admin, make_user):
    u = make_user("researcher@archive.org")
    assert u.get(f"{API}/admin/model-registry/preview", params={"identifier": "14543904"}).status_code == 403
    assert u.post(f"{API}/admin/model-registry/install", json={"identifier": "14543904", "accept_license": True}).status_code == 403


def test_install_versions_update_check_pinning_and_rollback(admin, db):
    # 1. Install CATMuS Medieval 1.5.0 (real 16 MB download, MD5 verified, loaded by Kraken).
    r = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "10.5281/zenodo.12743230", "accept_license": True})
    assert r.status_code == 202, r.text
    v150 = admin.get(f"{API}/admin/model-versions/{r.json()['id']}").json()
    assert v150["status"] == "validated", v150["install_log"]
    assert v150["version"] == "1.5.0" and v150["license_id"] == "cc-by-4.0" and v150["kraken_version_validated"] == "7.1.1"
    assert v150["bytes_downloaded"] == v150["size_bytes"] == 16332989
    assert v150["source_checksum"] == "md5:02bb2e4e5bc681c11579a860c40dc5a4" and len(v150["sha256"]) == 64
    assert v150["license_accepted_at"] and v150["unicode_normalization"] == "NFD"
    msgs = [e["message"] for e in v150["install_log"]]
    assert "download complete; size and checksum match the Zenodo record" in msgs and "validated: model loaded by Kraken" in msgs
    # The 1.5.0 card is schema-valid; its single author entry concatenates all names (shown verbatim, not reinterpreted).
    assert v150["model_card"]["valid"] is True and " and " in v150["model_card"]["card"]["authors"][0]["name"]

    model = db.scalar(select(RecognitionModel).where(RecognitionModel.concept_record_id == "10066218"))
    assert model.family == "CATMuS" and str(model.active_version_id) == v150["id"]
    assert model.curated["basis"].startswith("Zenodo record description")

    # 2. Update check: a newer, different binary exists → reported, NOT installed.
    upd = admin.post(f"{API}/admin/models/{model.id}/check-updates").json()
    assert upd["update_available"] is True and upd["latest_version"] != "1.5.0"
    assert db.scalar(select(ModelVersion).where(ModelVersion.model_id == model.id).where(ModelVersion.version != "1.5.0")) is None

    # 3. Install the latest version through the concept DOI; the active version must NOT change silently.
    latest = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "10.5281/zenodo.10066218", "accept_license": True}).json()
    vlatest = admin.get(f"{API}/admin/model-versions/{latest['id']}").json()
    assert vlatest["status"] == "validated" and vlatest["source_checksum"] == "md5:326a6ea675661ec9ce0bb6b1958de495"
    db.expire_all()
    assert str(db.get(RecognitionModel, model.id).active_version_id) == v150["id"]
    upd2 = admin.post(f"{API}/admin/models/{model.id}/check-updates").json()
    assert upd2["update_available"] is False
    models = admin.get(f"{API}/model-registry/models").json()
    cat = next(m for m in models if m["id"] == str(model.id))
    assert "lat" in cat["source_languages"]  # from the v1 model card of the latest record

    # 4. Pin the latest, then roll back to 1.5.0.
    assert admin.post(f"{API}/admin/models/{model.id}/active-version", json={"version_id": vlatest["id"]}).status_code == 200
    rb = admin.post(f"{API}/admin/models/{model.id}/active-version", json={"version_id": v150["id"]}).json()
    assert rb["active_version_id"] == v150["id"]
    audit = admin.get(f"{API}/audit-logs", params={"action": "model.activate_version"}).json()
    assert audit["total"] == 2

    # 5. The 1.6.0 record publishes the same binary as the latest: stored once, flagged as identical.
    if vlatest["version"] != "1.6.0":
        r160 = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "15030337", "accept_license": True}).json()
        v160 = admin.get(f"{API}/admin/model-versions/{r160['id']}").json()
        assert v160["status"] == "validated" and v160["sha256"] == vlatest["sha256"]
        assert any("identical binary" in e["message"] for e in v160["install_log"])

    # 6. Re-installing an installed record is refused.
    again = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "12743230", "accept_license": True})
    assert again.status_code == 409


def test_discovery_describes_each_catmus_record_independently(admin):
    d = admin.get(f"{API}/admin/model-registry/discover", params={"q": "CATMuS"}).json()
    assert d["total"] >= 3
    titles = {i["title"]: i for i in d["items"]}
    assert any("CATMuS" in t for t in titles)
    for item in d["items"]:
        has_kraken = any(f["kraken_model"] for f in item["files"])
        if not has_kraken:
            assert item["precheck"]["verdict"] == "incompatible"  # datasets, YOLO shards, papers…


def test_recommendation_uses_declared_facts_and_states_limitations(admin, traprinq, db):
    # TraPrInq was registered manually (fixture); an administrator curates it with an explicit basis.
    m = db.scalar(select(RecognitionModel).where(RecognitionModel.slug == "traprinq"))
    admin.patch(f"{API}/admin/models/{m.id}/curation", json={
        "languages": ["por"], "period_start": 1500, "period_end": 1899, "script_type": "manuscript",
        "basis": "Zenodo record title (16th–19th c. Portuguese handwriting)"})
    admin.post(f"{API}/admin/model-registry/install", json={"identifier": "10.5281/zenodo.10066218", "accept_license": True})

    pt = admin.get(f"{API}/model-registry/recommend",
                   params={"languages": "por", "script_type": "manuscript", "period_start": 1650, "period_end": 1660}).json()
    assert pt["items"][0]["name"] == "TraPrInq"
    first = pt["items"][0]
    assert {x["criterion"] for x in first["matches"]} == {"language", "script_type", "period"}
    assert all(x["evidence"] == "curated" for x in first["matches"])
    assert any("does not establish" in s for s in first["limitations"])

    lat = admin.get(f"{API}/model-registry/recommend",
                    params={"languages": "lat", "script_type": "manuscript", "period_start": 1200, "period_end": 1300}).json()
    top = lat["items"][0]
    assert "CATMuS" in top["name"]
    assert next(x for x in top["matches"] if x["criterion"] == "language")["evidence"] == "source model card"
    tp = next(i for i in lat["items"] if i["name"] == "TraPrInq")
    assert any(x["criterion"] == "language" for x in tp["mismatches"])


def test_real_catmus_job_on_a_medieval_occitan_page(admin, blla, db):
    """CATMuS Medieval installed through the registry recognises a 13th-c. Occitan bifolium
    (BnF fr. 14771, HTRomance medieval-occitan v0.0.1, Zenodo 22656364, CC-BY-4.0)."""
    import json as _json

    from tests.conftest import OCCITAN

    if not OCCITAN.is_file():
        pytest.skip(f"Occitan sample not available at {OCCITAN}")
    mv = admin.post(f"{API}/admin/model-registry/install", json={"identifier": "10.5281/zenodo.10066218", "accept_license": True}).json()
    assert admin.get(f"{API}/admin/model-versions/{mv['id']}").json()["status"] == "validated"
    p = admin.post(f"{API}/projects", json={"title": "Occitan", "languages": ["pro"]}).json()
    c = admin.post(f"{API}/projects/{p['id']}/collections", json={"title": "BnF fr. 14771"}).json()
    d = admin.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
                   files=[("files", (OCCITAN.name, OCCITAN.read_bytes(), "image/jpeg"))],
                   data={"metadata": _json.dumps({"archival_reference": "BnF fr. 14771"})}).json()[0]
    page = admin.get(f"{API}/documents/{d['id']}").json()["pages"][0]
    r = admin.post(f"{API}/projects/{p['id']}/recognition-jobs",
                   json={"page_ids": [page["id"]], "recognition_model_version_id": mv["id"]}).json()
    job = admin.get(f"{API}/recognition-jobs/{r['jobs'][0]['id']}").json()
    assert job["status"] == "completed", job["error"]
    rec = admin.get(f"{API}/recognitions/{job['output_recognition_id']}").json()
    assert rec["model"].startswith("CATMuS Medieval") and rec["unicode_normalization"] == "NFD"
    text = "\n".join(l["text"] for l in rec["lines"])
    assert rec["line_count"] > 60 and len(text) > 1500
    print(f"\n[CATMuS] {rec['line_count']} lines; sample: {[l['text'] for l in rec['lines'][3:8]]}")
    # Graphematic output: abbreviation signs kept as characters, not expanded.
    assert any(ch in text for ch in "⁊ꝑꝓ̃")
    # Provenance: recognition → model version → Zenodo record with licence acceptance.
    v = admin.get(f"{API}/admin/model-versions/{mv['id']}").json()
    assert v["doi"].startswith("10.5281/zenodo.") and v["license_accepted_at"] and v["source_metadata"]["zenodo"]["record_id"]
