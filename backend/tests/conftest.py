"""Test configuration.

Integration tests run against a REAL PostgreSQL database (scriptorium_test) and a
REAL Redis (db 15). The schema is built with the production Alembic migrations,
not with metadata.create_all, so migrations are tested too.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="scriptorium-test-"))
os.environ.update({
    "SCRIPTORIUM_ENVIRONMENT": "test",
    "SCRIPTORIUM_DATABASE_URL": os.environ.get(
        "SCRIPTORIUM_TEST_DATABASE_URL", "postgresql+psycopg://scriptorium:scriptorium_dev@localhost:5432/scriptorium_test"),
    "SCRIPTORIUM_REDIS_URL": os.environ.get("SCRIPTORIUM_TEST_REDIS_URL", "redis://localhost:6379/15"),
    "SCRIPTORIUM_STORAGE_LOCAL_ROOT": str(_TMP / "storage"),
    "SCRIPTORIUM_MAIL_OUTBOX_DIR": str(_TMP / "outbox"),
    "SCRIPTORIUM_MODEL_DIR": str(_TMP / "models"),
    "SCRIPTORIUM_TRAINING_DIR": str(_TMP / "training"),
    "SCRIPTORIUM_LOGIN_MAX_FAILURES": "5",
    "SCRIPTORIUM_LOG_LEVEL": "WARNING",
    # Tasks run in-process so integration tests are deterministic; the real Celery
    # worker path is exercised by test_celery_worker_round_trip_through_real_redis and E2E.
    "SCRIPTORIUM_CELERY_EAGER": "true",
})
REPO = Path(__file__).resolve().parents[2]
TRAPRINQ_FILE = Path(os.environ.get("SCRIPTORIUM_TEST_TRAPRINQ", REPO / "var/downloads/traprinq-model/traprinq.mlmodel"))
TRAPRINQ_MD5 = "md5:b7f84ede75a0665c275346d94fdc1b95"  # published on Zenodo record 14543904
SAMPLES = Path(os.environ.get("SCRIPTORIUM_TEST_SAMPLES", REPO / "var/samples/traprinq-val"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_engine, session_factory  # noqa: E402
from app.services.throttle import get_redis  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    engine = get_engine()
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean():
    with get_engine().begin() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        # TRUNCATE is statement-level, so the append-only row trigger does not fire.
        c.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    get_redis().flushdb()
    outbox = _TMP / "outbox"
    if outbox.exists():
        shutil.rmtree(outbox)
    yield


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def outbox() -> Path:
    return _TMP / "outbox"


class Api:
    """Small helper representing one logged-in user with their own cookie jar."""

    def __init__(self, email: str, full_name: str = "Test User"):
        from app.main import app

        self.client = TestClient(app)
        self.email = email
        r = self.client.post("/api/v1/auth/register", json={"email": email, "full_name": full_name, "password": PASSWORD})
        assert r.status_code == 201, r.text
        self.user = r.json()
        self.login()

    def login(self):
        r = self.client.post("/api/v1/auth/login", json={"email": self.email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.token = r.json()["access_token"]
        return r

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.client.get(url, headers=self.h, **kw)

    def post(self, url, **kw):
        return self.client.post(url, headers=self.h, **kw)

    def put(self, url, **kw):
        return self.client.put(url, headers=self.h, **kw)

    def patch(self, url, **kw):
        return self.client.patch(url, headers=self.h, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self.h, **kw)


@pytest.fixture
def make_user():
    created = []

    def _make(email: str, full_name: str = "Test User") -> Api:
        a = Api(email, full_name)
        created.append(a)
        return a

    yield _make
    for a in created:
        a.client.close()


@pytest.fixture
def blla(db):
    """Kraken's bundled segmentation model, registered and validated for real."""
    from app.registry.local import register_bundled_segmentation

    mv = register_bundled_segmentation(db)
    db.commit()
    return mv


@pytest.fixture
def traprinq(db):
    """The real TraPrInq model file downloaded from Zenodo (MD5 verified on registration)."""
    if not TRAPRINQ_FILE.is_file():
        pytest.skip(f"TraPrInq model file not available at {TRAPRINQ_FILE} "
                    "(download it from https://zenodo.org/records/14543904 or set SCRIPTORIUM_TEST_TRAPRINQ)")
    from app.models import ModelTask
    from app.registry.local import register_file, validate

    mv = register_file(db, src=TRAPRINQ_FILE, slug="traprinq", name="TraPrInq", task=ModelTask.RECOGNITION, version="V1",
                       doi="10.5281/zenodo.14543904", license_id="cc-by-4.0", source_checksum=TRAPRINQ_MD5,
                       unicode_normalization="NFD")
    db.commit()
    validate(db, mv)
    db.commit()
    return mv


CATMUS150_MD5 = "md5:02bb2e4e5bc681c11579a860c40dc5a4"  # CATMuS Medieval 1.5.0, Zenodo record 12743230


@pytest.fixture
def catmus(db):
    """The real CATMuS Medieval 1.5.0 model file (downloaded from Zenodo by the registry; located by its MD5)."""
    import hashlib

    want = CATMUS150_MD5.split(":")[1]
    env = os.environ.get("SCRIPTORIUM_TEST_CATMUS")
    cands = [Path(env)] if env else sorted((REPO / "var/models").glob("*/catmus*.mlmodel"))
    src = next((c for c in cands if c.is_file() and c.stat().st_size == 16332989
                and hashlib.md5(c.read_bytes()).hexdigest() == want), None)
    if src is None:
        pytest.skip("CATMuS Medieval 1.5.0 model file not available (install it from Zenodo 12743230 or set SCRIPTORIUM_TEST_CATMUS)")
    from app.models import ModelTask
    from app.registry.local import register_file, validate

    mv = register_file(db, src=src, slug="catmus-medieval", name="CATMuS Medieval", task=ModelTask.RECOGNITION,
                       version="1.5.0", doi="10.5281/zenodo.12743230", license_id="cc-by-4.0",
                       source_checksum=CATMUS150_MD5, unicode_normalization="NFD")
    db.commit()
    validate(db, mv)
    db.commit()
    return mv


OCCITAN = Path(os.environ.get("SCRIPTORIUM_TEST_OCCITAN", REPO / "var/samples/occitan/btv1b9061575q_f6.jpg"))


def sample_page(name: str = "PT-TT-TSO-IL-28-7926_m0052") -> Path:
    p = SAMPLES / f"{name}.jpg"
    if not p.is_file():
        pytest.skip(f"Sample manuscript page not available at {p} (TraPrInq validation set, Zenodo record 13986218)")
    return p
