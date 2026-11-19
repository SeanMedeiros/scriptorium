"""Job management, model registration and validation (Kraken loads, no inference)."""
import io
import shutil
import uuid

import pytest
from PIL import Image
from sqlalchemy import text

from app.models import ModelStatus, ModelTask
from app.registry.local import ModelInstallError, register_file, validate
from app.worker.pipeline import run_recognition_job

pytestmark = pytest.mark.integration
API = "/api/v1"


@pytest.fixture
def setup(make_user):
    u = make_user("pi@archive.org")
    p = u.post(f"{API}/projects", json={"title": "Jobs"}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "C"}).json()
    pages = []
    for i in range(2):
        b = io.BytesIO()
        Image.new("L", (300, 200), 200 + i).save(b, format="PNG")
        d = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
                   files=[("files", (f"p{i}.png", b.getvalue(), "image/png"))]).json()[0]
        pages.append(u.get(f"{API}/documents/{d['id']}").json()["pages"][0])
    return u, p, c, pages


@pytest.mark.kraken
def test_bundled_segmenter_is_registered_and_validated_by_loading(blla):
    assert blla.status == ModelStatus.VALIDATED and blla.kraken_version_validated == "7.1.1"
    assert blla.file_metadata["networks"][0]["model_type"] == ["segmentation"]
    assert blla.license_id == "Apache-2.0"


@pytest.mark.kraken
def test_checksum_mismatch_blocks_installation(db, tmp_path, blla):
    src = tmp_path / "copy.mlmodel"
    shutil.copy(blla.file_path, src)
    with pytest.raises(ModelInstallError) as e:
        register_file(db, src=src, slug="x", name="X", task=ModelTask.SEGMENTATION, version="1",
                      source_checksum="md5:00000000000000000000000000000000")
    assert e.value.code == "checksum_mismatch"
    db.rollback()


@pytest.mark.kraken
def test_non_model_file_is_marked_incompatible(db, tmp_path):
    bogus = tmp_path / "not-a-model.mlmodel"
    bogus.write_bytes(b"this is not a neural network" * 100)
    mv = register_file(db, src=bogus, slug="bogus", name="Bogus", task=ModelTask.RECOGNITION, version="0")
    validate(db, mv)
    db.commit()
    assert mv.status == ModelStatus.INCOMPATIBLE and mv.validated_at is None
    assert mv.install_log[-1]["message"] == "validation failed"


@pytest.mark.kraken
def test_segmentation_model_rejected_as_recognition_model(db, tmp_path, blla):
    src = tmp_path / "seg.mlmodel"
    shutil.copy(blla.file_path, src)
    mv = register_file(db, src=src, slug="wrong", name="Wrong", task=ModelTask.RECOGNITION, version="1")
    validate(db, mv)
    assert mv.status == ModelStatus.INCOMPATIBLE


def test_job_requires_validated_model(setup, blla, db):
    u, p, c, pages = setup
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
               json={"page_ids": [pages[0]["id"]], "recognition_model_version_id": str(blla.id)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_model"
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
               json={"page_ids": [pages[0]["id"]], "recognition_model_version_id": str(uuid.uuid4())})
    assert r.status_code == 422


def test_cannot_submit_pages_of_another_project(setup, make_user, blla):
    u, p, c, pages = setup
    other = u.post(f"{API}/projects", json={"title": "Other"}).json()
    r = u.post(f"{API}/projects/{other['id']}/recognition-jobs", json={"page_ids": [pages[0]["id"]], "job_type": "segment"})
    assert r.status_code == 404


def test_reader_cannot_submit_jobs(setup, make_user, blla):
    u, p, c, pages = setup
    reader = make_user("reader@archive.org")
    u.put(f"{API}/projects/{p['id']}/members", json={"email": reader.email, "role": "read_only"})
    r = reader.post(f"{API}/projects/{p['id']}/recognition-jobs", json={"page_ids": [pages[0]["id"]], "job_type": "segment"})
    assert r.status_code == 403


def test_cancel_queued_job_and_worker_skips_it(setup, db, monkeypatch, blla):
    u, p, c, pages = setup
    import app.api.recognition as rec_api

    monkeypatch.setattr(rec_api, "_enqueue", lambda job: None)  # keep jobs queued (no worker)
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
               json={"document_ids": [pages[0]["document_id"], pages[1]["document_id"]], "job_type": "segment"})
    assert r.status_code == 201
    batch = r.json()
    assert len(batch["jobs"]) == 2 and len({j["batch_id"] for j in batch["jobs"]}) == 1
    jid = batch["jobs"][0]["id"]
    cancelled = u.post(f"{API}/recognition-jobs/{jid}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert u.post(f"{API}/recognition-jobs/{jid}/cancel").status_code == 409
    assert run_recognition_job(uuid.UUID(jid)) == {"skipped": True, "reason": "not queued"}
    listing = u.get(f"{API}/projects/{p['id']}/recognition-jobs").json()
    assert listing["counts"]["cancelled"] == 1 and listing["counts"]["queued"] == 1
    assert u.get(f"{API}/projects/{p['id']}/recognition-jobs", params={"status": "queued"}).json()["total"] == 1
    # Retrying the cancelled job re-queues it as a new job.
    rr = u.post(f"{API}/recognition-jobs/{jid}/retry")
    assert rr.status_code == 201 and rr.json()["status"] == "queued"


def test_active_job_quota(setup, monkeypatch, blla):
    from app.config import get_settings
    import app.api.recognition as rec_api

    u, p, c, pages = setup
    monkeypatch.setattr(rec_api, "_enqueue", lambda job: None)
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={"page_ids": [x["id"] for x in pages], "job_type": "segment"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_active_jobs"


@pytest.mark.kraken
def test_segmentation_only_job_on_synthetic_lines(setup, blla, db):
    """Real Kraken segmentation (bundled model) on a synthetic image with dark line strokes."""
    u, p, c, _ = setup
    from PIL import ImageDraw

    im = Image.new("L", (1200, 900), 235)
    d = ImageDraw.Draw(im)
    for y in range(100, 820, 70):
        for x in range(100, 1080, 60):
            d.ellipse([x, y, x + 40, y + 28], outline=30, width=4)
    b = io.BytesIO()
    im.save(b, format="PNG")
    doc = u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents",
                 files=[("files", ("synthetic.png", b.getvalue(), "image/png"))]).json()[0]
    page = u.get(f"{API}/documents/{doc['id']}").json()["pages"][0]
    r = u.post(f"{API}/projects/{p['id']}/recognition-jobs", json={"page_ids": [page["id"]], "job_type": "segment"}).json()
    job = u.get(f"{API}/recognition-jobs/{r['jobs'][0]['id']}").json()
    # Either Kraken finds the stroke rows (completed, segmentation only) or reports no lines — never a fake result.
    assert job["status"] in ("completed", "failed")
    if job["status"] == "completed":
        assert job["output_recognition_id"] is None
        seg = u.get(f"{API}/segmentations/{job['output_segmentation_id']}").json()
        assert seg["input_asset"]["id"] == page["image_asset_id"]  # no preprocessing → original pixels
        assert [ln["order_index"] for ln in seg["lines"]] == list(range(len(seg["lines"])))
    else:
        assert job["error"]["code"] == "no_lines_detected"


def test_stale_processing_job_is_requeued_and_rerun(setup, db, monkeypatch, blla):
    """Simulates a worker killed mid-job: no heartbeat for longer than the threshold."""
    import app.api.recognition as rec_api
    from app.worker.pipeline import recover_stale_jobs

    u, p, c, pages = setup
    monkeypatch.setattr(rec_api, "_enqueue", lambda job: None)
    jobs = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
                  json={"page_ids": [pages[0]["id"], pages[1]["id"]], "job_type": "segment"}).json()["jobs"]
    stale, alive = jobs[0]["id"], jobs[1]["id"]
    db.execute(text("UPDATE recognition_jobs SET status='processing', attempts=1, worker='lost-node', stage='segmentation', "
                    "heartbeat_at = now() - interval '10 minutes' WHERE id = :i"), {"i": stale})
    db.execute(text("UPDATE recognition_jobs SET status='processing', attempts=1, worker='busy-node', "
                    "heartbeat_at = now() WHERE id = :i"), {"i": alive})
    db.commit()
    assert recover_stale_jobs() == [stale]  # eager mode: the re-queued job runs again immediately
    j = u.get(f"{API}/recognition-jobs/{stale}").json()
    assert any("re-queued" in e["message"] for e in j["log"])
    assert j["attempts"] == 2 and j["status"] in ("completed", "failed")  # really executed again
    assert u.get(f"{API}/recognition-jobs/{alive}").json()["status"] == "processing"  # live job untouched


def test_job_lost_too_many_times_fails_explicitly(setup, db, monkeypatch, blla):
    import app.api.recognition as rec_api
    from app.worker.pipeline import recover_stale_jobs

    u, p, c, pages = setup
    monkeypatch.setattr(rec_api, "_enqueue", lambda job: None)
    jid = u.post(f"{API}/projects/{p['id']}/recognition-jobs",
                 json={"page_ids": [pages[0]["id"]], "job_type": "segment"}).json()["jobs"][0]["id"]
    db.execute(text("UPDATE recognition_jobs SET status='processing', attempts=3, stage='recognition', "
                    "heartbeat_at = now() - interval '1 hour' WHERE id = :i"), {"i": jid})
    db.commit()
    assert recover_stale_jobs() == []
    j = u.get(f"{API}/recognition-jobs/{jid}").json()
    assert j["status"] == "failed" and j["error"]["code"] == "worker_lost" and j["error"]["stage"] == "recognition"


@pytest.mark.kraken
def test_models_listing_after_registry_schema(setup, blla, traprinq):
    """Regression: the active-version FK made model joins ambiguous (HTTP 500 on /models)."""
    u = setup[0]
    r = u.get(f"{API}/models", params={"usable": "true"})
    assert r.status_code == 200
    assert {m["model_slug"] for m in r.json()} == {"kraken-blla", "traprinq"}
    assert u.get(f"{API}/models", params={"task": "recognition"}).json()[0]["model_slug"] == "traprinq"
