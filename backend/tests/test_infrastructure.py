import io
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.storage import LocalFilesystemBackend, ObjectNotFound

API = "/api/v1"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_health_ready_checks_real_dependencies(client):
    r = client.get("/api/health/ready")
    body = r.json()
    assert r.status_code == 200, body
    assert body["checks"]["database"]["schema_revision"]
    assert {k: v["status"] for k, v in body["checks"].items()} == {"database": "ok", "redis": "ok", "storage": "ok",
                                                                  "malware_scanner": "ok"}
    assert body["checks"]["malware_scanner"]["scanner"] == "disabled"  # tests default; clamd covered in test_phase7


@pytest.mark.integration
def test_openapi_documents_core_endpoints(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    for p in ("/api/v1/auth/login", "/api/v1/projects", "/api/v1/projects/{project_id}/collections", "/api/v1/audit-logs"):
        assert p in paths


@pytest.mark.integration
def test_request_id_propagation_and_security_headers(client):
    r = client.get("/api/health/live", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_local_storage_is_content_addressed_and_write_once(tmp_path):
    s = LocalFilesystemBackend(tmp_path)
    data = b"\x89PNG fake bytes of a folio"
    a = s.put_stream("originals", io.BytesIO(data))
    b = s.put_stream("originals", io.BytesIO(data))
    assert a == b and a.size == len(data)
    assert a.key.startswith(f"originals/{a.sha256[:2]}/")
    assert b"".join(s.iter_bytes(a.key)) == data
    mode = stat.S_IMODE(os.stat(tmp_path / a.key).st_mode)
    assert mode & 0o222 == 0, "stored originals must be read-only on disk"
    assert not list(tmp_path.rglob(".upload-*")), "no temp files left behind"


def test_local_storage_rejects_traversal(tmp_path):
    s = LocalFilesystemBackend(tmp_path / "root")
    with pytest.raises(ValueError):
        s.open("../../etc/passwd")
    with pytest.raises(ValueError):
        s.put_stream("../escape", io.BytesIO(b"x"))
    with pytest.raises(ObjectNotFound):
        s.open("originals/aa/missing")


@pytest.mark.fixture_only
def test_s3_backend_against_moto_mock():
    """FIXTURE-ONLY: exercises the S3 code path against moto's in-process mock,
    not a real MinIO server (MinIO is not reachable in the build environment)."""
    moto = pytest.importorskip("moto")
    from app.storage import S3Backend

    with moto.mock_aws():
        s = S3Backend(bucket="scriptorium-test", endpoint_url=None, access_key="x", secret_key="x", region="us-east-1")
        s.ensure_bucket()
        obj = s.put_stream("originals", io.BytesIO(b"folio"))
        assert s.exists(obj.key) and s.size(obj.key) == 5
        assert b"".join(s.iter_bytes(obj.key)) == b"folio"
        s.healthcheck()


@pytest.mark.integration
def test_celery_worker_round_trip_through_real_redis():
    """Starts a real Celery worker process and sends a task through Redis (eager mode disabled)."""
    from app.worker.celery_app import celery_app

    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = False
    env = {**os.environ, "SCRIPTORIUM_CELERY_EAGER": "false"}
    worker = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "app.worker.celery_app", "worker", "-Q", "default", "-c", "1",
         "--pool", "solo", "--loglevel", "WARNING", "-n", f"pytest-{os.getpid()}@%h"],
        cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        from app.worker.celery_app import ping

        deadline = time.time() + 60
        result = ping.delay()
        out = result.get(timeout=max(1, deadline - time.time()))
        assert out["pong"] is True and out["version"]
        assert out["host"] and result.state == "SUCCESS"  # executed by the worker process, not eagerly
    finally:
        celery_app.conf.task_always_eager = previous
        worker.terminate()
        worker.wait(timeout=20)


def test_production_settings_refuse_insecure_defaults(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production")
    ok = Settings(environment="production", secret_key="x" * 40, cookie_secure=True, mail_backend="smtp")
    assert ok.environment == "production"
