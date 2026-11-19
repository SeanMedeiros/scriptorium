"""REAL Kraken fine-tuning through the API (Phase 6). Nothing is mocked.

Data: three pages of the TraPrInq Validation_Set_FINAL (Zenodo 13986218, CC-BY-4.0) with their
Transkribus ground truth — 687_m0019 (40 lines) → train, 12193_m0197 (10 lines) → validation,
7926_m0052 (18 lines) → test. Base model: TraPrInq V1 (Zenodo 14543904). One epoch on 2 CPU
cores (≈2 min). The test page is never given to the training process; it is evaluated afterwards.
"""
import io
import json
import zipfile

import pytest
import yaml

from tests.conftest import sample_page

pytestmark = [pytest.mark.integration, pytest.mark.kraken]
API = "/api/v1"
SPLITS = {"PT-TT-TSO-IL-28-687_m0019": "train", "PT-TT-TSO-IL-28-12193_m0197": "validation",
          "PT-TT-TSO-IL-28-7926_m0052": "test"}


@pytest.fixture
def training_project(make_user, traprinq, blla):
    u = make_user("pi.treino@uc.pt", "Investigadora Principal")
    p = u.post(f"{API}/projects", json={"title": "Afinação TraPrInq", "languages": ["por"]}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Inquisição"}).json()
    ds = u.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "Treino"}).json()
    for name, split in SPLITS.items():
        path = sample_page(name)
        d = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
                   files=[("files", (path.name, path.read_bytes(), "image/jpeg"))]).json()[0]
        page = u.get(f"{API}/documents/{d['id']}").json()["pages"][0]
        r = u.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/import", data={"page_id": page["id"], "split": split},
                   files={"file": (f"{name}.xml", path.with_suffix(".xml").read_bytes(), "application/xml")})
        assert r.status_code == 201, r.text
    return u, p, ds


def test_real_fine_tuning_registers_a_validated_institutional_model(training_project, traprinq, make_user, db):
    u, p, ds = training_project
    r = u.post(f"{API}/projects/{p['id']}/training-jobs", json={
        "dataset_id": ds["id"], "base_model_version_id": str(traprinq.id), "output_name": "TraPrInq afinado Inquisição",
        "epochs": 1, "quit": "fixed", "lrate": 1e-4, "seed": 42, "evaluate_test": True})
    assert r.status_code == 201, r.text
    job = u.get(f"{API}/training-jobs/{r.json()['id']}").json()
    assert job["status"] == "completed", (job["error"], job["log"][-5:])
    # Data actually used: train and validation splits only; the test page was never prepared.
    man = job["data_manifest"]
    assert man["splits"]["train"]["lines"] == 40 and man["splits"]["validation"]["lines"] == 10
    assert set(man["splits"]) == {"train", "validation"} and man["test_split_used"] is False
    assert man["normalization"] == "NFD" and man["seed"] == 42 and man["software"]["kraken"] == "7.1.1"
    assert job["config"]["normalization"] == "NFD"  # inherited from TraPrInq's documented convention
    # One validated epoch with Kraken's metrics, and a kept checkpoint.
    assert len(job["epochs"]) == 1 and 0.5 < job["epochs"][0]["val_accuracy"] <= 1
    assert job["checkpoints"] and job["checkpoints"][0]["epoch"] == 1 and job["best_checkpoint"]
    print(f"\n[real] fine-tuning 1 epoch: validation accuracy {job['epochs'][0]['val_accuracy']:.4f}, "
          f"{job['duration_s']:.0f} s; test CER {job['test_evaluation']['cer']:.4f}")
    # The new model: trained, owned by the project, validated by Kraken, with full provenance.
    mv = u.get(f"{API}/institutional-models/{job['output_model_version_id']}").json()
    assert mv["status"] == "validated" and mv["source"] == "trained" and mv["owner_project_id"] == p["id"]
    assert mv["is_active"] and mv["kraken_version_validated"] == "7.1.1"
    prov = mv["provenance"]
    assert prov["kind"] == "fine-tuned" and prov["base_model"]["doi"] == "10.5281/zenodo.14543904"
    assert "licence terms apply" in prov["attribution_note"]
    assert prov["validation_accuracy"] == pytest.approx(job["epochs"][0]["val_accuracy"], abs=1e-4)
    # Automatic evaluation on the held-out test page with the Phase 5 methodology.
    te = job["test_evaluation"]
    assert te["pages_scored"] == 1 and 0 <= te["cer"] < 0.5
    # Scoping: usable in its project only, invisible elsewhere.
    assert any(m["id"] == mv["id"] for m in u.get(f"{API}/models?task=recognition&usable=true").json())
    other = make_user("outro@ox.ac.uk")
    op = other.post(f"{API}/projects", json={"title": "Other"}).json()
    assert all(m["id"] != mv["id"] for m in other.get(f"{API}/models?task=recognition").json())
    assert other.get(f"{API}/models/{mv['id']}").status_code == 404
    page_other = None
    assert other.post(f"{API}/projects/{op['id']}/recognition-jobs", json={
        "page_ids": [], "document_ids": [], "recognition_model_version_id": mv["id"]}).status_code in (404, 422)
    # Weights download for project members; checksum matches.
    w = u.get(f"{API}/institutional-models/{mv['id']}/download")
    import hashlib

    assert w.status_code == 200 and hashlib.sha256(w.content).hexdigest() == mv["sha256"]
    assert other.get(f"{API}/institutional-models/{mv['id']}/download").status_code == 404
    # Publication: licence compatibility, PI approval, valid htrmopo card, Zenodo metadata.
    bad = u.post(f"{API}/institutional-models/{mv['id']}/publications", json={
        "title": "TraPrInq fine-tuned", "description": "Fine-tuned on Inquisition records of Coimbra.",
        "authors": [{"name": "Investigadora Principal"}], "license_id": "all-rights-reserved"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "licence_incompatible"
    pub = u.post(f"{API}/institutional-models/{mv['id']}/publications", json={
        "title": "TraPrInq fine-tuned on Coimbra Inquisition records",
        "description": "Kraken recognition model fine-tuned from TraPrInq V1 on SCRIPTORIUM ground truth.",
        "authors": [{"name": "Investigadora Principal", "affiliation": "Universidade de Coimbra"}],
        "license_id": "cc-by-4.0", "keywords": ["Portuguese", "Inquisition"]}).json()
    ap = u.post(f"{API}/model-publications/{pub['id']}/approve", json={"statement": "All listed authors approved publication."})
    assert ap.status_code == 200, ap.text
    assert ap.json()["model_card_validation"]["valid"], ap.json()["model_card_validation"]
    z = zipfile.ZipFile(io.BytesIO(u.get(f"{API}/model-publications/{pub['id']}/package").content))
    names = set(z.namelist())
    assert {"README.md", "zenodo.json", "PROVENANCE.json", "PUBLISHING.txt"} <= names and any(n.endswith(".safetensors") for n in names)
    card = yaml.safe_load(z.read("README.md").decode().split("---")[1])
    assert card["base_model"] == ["https://doi.org/10.5281/zenodo.14543904"] and card["language"] == ["por"]
    zen = json.loads(z.read("zenodo.json"))["metadata"]
    assert zen["related_identifiers"][0] == {"identifier": "10.5281/zenodo.14543904", "relation": "isDerivedFrom", "scheme": "doi"}
    # A second approval is refused; the platform never claims a DOI it did not receive.
    assert u.post(f"{API}/model-publications/{pub['id']}/approve", json={"statement": "again, again"}).status_code == 409
    assert mv.get("doi") is None
    del page_other


def test_training_request_validation(training_project, traprinq, make_user):
    u, p, ds = training_project
    base = {"dataset_id": ds["id"], "base_model_version_id": str(traprinq.id), "output_name": "X model", "epochs": 1}
    assert u.post(f"{API}/projects/{p['id']}/training-jobs", json={**base, "epochs": 100000}).status_code == 422
    empty = u.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "Empty"}).json()
    r = u.post(f"{API}/projects/{p['id']}/training-jobs", json={**base, "dataset_id": empty["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_training_data"
    ro = make_user("leitor@uc.pt")
    u.put(f"{API}/projects/{p['id']}/members", json={"email": ro.email, "role": "transcription_editor"})
    assert ro.post(f"{API}/projects/{p['id']}/training-jobs", json=base).status_code == 403
    assert ro.get(f"{API}/projects/{p['id']}/training-jobs").status_code == 200


def test_real_training_can_be_cancelled_and_registers_nothing(training_project, traprinq, db, monkeypatch):
    """The job is created through the API with the Celery task suppressed, then run by the real
    worker function in a thread while the API cancels it."""
    import threading
    import time
    import uuid

    from app.db import session_factory
    from app.models import ModelVersion, TrainingJob, TrainingStatus
    from app.storage import get_storage
    from app.training.service import run_training
    from app.worker import tasks

    u, p, ds = training_project
    monkeypatch.setattr(tasks.run_training, "apply_async", lambda *a, **k: None)
    r = u.post(f"{API}/projects/{p['id']}/training-jobs", json={
        "dataset_id": ds["id"], "base_model_version_id": str(traprinq.id), "output_name": "Cancelled model",
        "epochs": 5, "quit": "fixed", "evaluate_test": False})
    jid = uuid.UUID(r.json()["id"])
    def work():
        with session_factory()() as s:  # the worker's session is closed, as in the Celery task
            run_training(s, jid, get_storage(), "test")

    t = threading.Thread(target=work, daemon=True)
    t.start()
    deadline = time.time() + 300
    while time.time() < deadline:
        if u.get(f"{API}/training-jobs/{jid}").json()["status"] == "training":
            break
        time.sleep(1)
    time.sleep(8)  # let the runner start its first batches
    c = u.post(f"{API}/training-jobs/{jid}/cancel")
    assert c.status_code == 200
    t.join(timeout=400)
    db.expire_all()
    job = db.get(TrainingJob, jid)
    assert job.status == TrainingStatus.CANCELLED and job.output_model_version_id is None
    assert any("cancellation requested" in e["message"] for e in job.log)
    assert db.query(ModelVersion).filter(ModelVersion.training_job_id == jid).count() == 0
    assert u.post(f"{API}/training-jobs/{jid}/cancel").status_code == 409
