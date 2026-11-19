"""Phase 7 — production readiness: HTTP security headers, REAL ClamAV malware scanning (fail-closed),
institutional storage policies and data retention.

The malware tests talk to a REAL clamd (ClamAV 1.x) over its INSTREAM protocol. Because the official
signature mirrors are not reachable from every test environment, the daemon is started by
scripts/clamd-test.sh with deploy/clamav/scriptorium-test.ndb — a one-line body signature for the
EICAR test string — in addition to any official databases present. The engine, protocol and
detection path are real; only the signature set is restricted.
"""
import io
import json
import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text

from app.config import get_settings
from app.services import retention
from tests.test_transcriptions import ws  # noqa: F401

pytestmark = pytest.mark.integration
API = "/api/v1"
REPO = Path(__file__).resolve().parents[2]
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _png(color=(240, 230, 210), size=(200, 120)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, format="PNG")
    return b.getvalue()


def _pdf_with(payload: bytes) -> bytes:
    """A syntactically minimal PDF whose stream carries `payload` (to test scanning before parsing)."""
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"3 0 obj<</Length " + str(len(payload)).encode() + b">>stream\n" + payload + b"\nendstream endobj\n%%EOF\n")


def _clamd_up(port=3310) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"zPING\0")
            return s.recv(16).startswith(b"PONG")
    except OSError:
        return False


@pytest.fixture(scope="session")
def clamd():
    if not _clamd_up():
        if not shutil.which("clamd"):
            pytest.skip("ClamAV (clamd) is not installed")
        subprocess.run([str(REPO / "scripts/clamd-test.sh")], check=True, timeout=180)
    assert _clamd_up()
    return "tcp://127.0.0.1:3310"


@pytest.fixture
def scanner(clamd, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "malware_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_address", clamd)
    return s


@pytest.fixture
def proj(make_user):
    u = make_user("curator@arquivo.pt", "Curator")
    p = u.post(f"{API}/projects", json={"title": "Scanned"}).json()
    c = u.post(f"{API}/projects/{p['id']}/collections", json={"title": "Box"}).json()
    return u, p, c


def _upload(u, p, c, name, data, mime):
    return u.post(f"{API}/projects/{p['id']}/collections/{c['id']}/documents", files=[("files", (name, data, mime))])


# ------------------------------------------------------------------------------------------ headers

def test_security_headers_and_self_hosted_api_docs(client):
    r = client.get("/api/health/live")
    h = r.headers
    assert h["content-security-policy"].startswith("default-src 'none'")
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "same-origin" and h["cross-origin-opener-policy"] == "same-origin"
    assert "strict-transport-security" not in h  # only when SCRIPTORIUM_HSTS_SECONDS > 0 (TLS deployments)
    assert client.post(f"{API}/auth/login", json={"email": "x@y.org", "password": "nope-nope-nope"}).headers["cache-control"] == "no-store"
    for page in ("/api/docs", "/api/redoc"):
        html = client.get(page).text
        assert "/api/static/apidocs/" in html
        assert not any(cdn in html for cdn in ("jsdelivr", "unpkg", "googleapis", "cdn.redoc.ly"))  # no third-party requests
    assert client.get("/api/static/apidocs/swagger-ui-bundle.js").status_code == 200


def test_hsts_emitted_when_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "hsts_seconds", 31536000)
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        assert c.get("/api/health/live").headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_production_refuses_wildcard_cors():
    from app.config import Settings

    with pytest.raises(ValueError, match="CORS"):
        Settings(environment="production", secret_key="x" * 40, cookie_secure=True, mail_backend="smtp", cors_origins=["*"])


# ------------------------------------------------------------------------------------------ malware

def test_clamd_detects_malware_and_upload_is_refused_audited_and_not_stored(proj, scanner, db):
    u, p, c = proj
    r = _upload(u, p, c, "carta.pdf", _pdf_with(EICAR), "application/pdf")
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "malware_detected" and err["details"]["engine"] == "clamd"
    assert err["details"]["signature"].startswith("Scriptorium.Test.EICAR")
    assert db.execute(text("SELECT count(*) FROM image_assets")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM documents")).scalar() == 0
    row = db.execute(text("SELECT details FROM audit_logs WHERE action='upload.malware_detected'")).one()
    assert row[0]["file"] == "carta.pdf" and row[0]["signature"].startswith("Scriptorium.Test.EICAR")
    # A clean file passes the same real scanner and is ingested.
    ok = _upload(u, p, c, "folio.png", _png(), "image/png")
    assert ok.status_code == 201, ok.text
    # Readiness reports the scanner engine version.
    ready = u.client.get("/api/health/ready").json()["checks"]["malware_scanner"]
    assert ready["status"] == "ok" and ready["version"].startswith("ClamAV")


def test_model_and_ground_truth_uploads_are_scanned(proj, scanner):
    u, p, c = proj
    r = u.post(f"{API}/projects/{p['id']}/institutional-models", data={"name": "Suspicious model", "version": "1"},
               files={"file": ("m.mlmodel", b"\0" * 1024 + EICAR, "application/octet-stream")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "malware_detected"
    d = _upload(u, p, c, "f.png", _png(), "image/png").json()[0]
    page = u.get(f"{API}/documents/{d['id']}").json()["pages"][0]
    ds = u.post(f"{API}/projects/{p['id']}/evaluation-datasets", json={"name": "GT"}).json()
    r = u.post(f"{API}/evaluation-datasets/{ds['id']}/ground-truth/import", data={"page_id": page["id"], "split": "test"},
               files={"file": ("gt.xml", b"<?xml version='1.0'?><!-- " + EICAR + b" --><x/>", "application/xml")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "malware_detected"


def test_scanner_unavailable_fails_closed(proj, monkeypatch, db):
    u, p, c = proj
    s = get_settings()
    monkeypatch.setattr(s, "malware_scanner", "clamd")
    monkeypatch.setattr(s, "clamd_address", "tcp://127.0.0.1:9")  # nothing listens here
    r = _upload(u, p, c, "folio.png", _png(), "image/png")
    assert r.status_code == 503 and r.json()["error"]["code"] == "malware_scanner_unavailable"
    assert db.execute(text("SELECT count(*) FROM documents")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action='upload.refused_scanner_unavailable'")).scalar() == 1
    ready = u.client.get("/api/health/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["malware_scanner"]["status"] == "error"


# ------------------------------------------------------------------------------------------ storage policy

@pytest.fixture
def inst(make_user):
    root = make_user("root@platform.org", "Root")
    admin = make_user("admin@bnp.pt", "BNP Admin")
    member = make_user("res@bnp.pt", "BNP Researcher")
    i = root.post(f"{API}/institutions", json={"name": "Biblioteca Nacional", "slug": "bnp", "country": "PT"}).json()
    root.put(f"{API}/institutions/{i['id']}/members", json={"email": admin.email, "role": "institution_admin"})
    admin.put(f"{API}/institutions/{i['id']}/members", json={"email": member.email, "role": "member"})
    p = member.post(f"{API}/projects", json={"title": "Reservados", "institution_id": i["id"]}).json()
    c = member.post(f"{API}/projects/{p['id']}/collections", json={"title": "Cod."}).json()
    return dict(root=root, admin=admin, member=member, inst=i, p=p, c=c)


def test_institutional_storage_policy_is_enforced_and_audited(inst, db):
    a, m, i, p, c = inst["admin"], inst["member"], inst["inst"], inst["p"], inst["c"]
    url = f"{API}/institutions/{i['id']}/storage-policy"
    assert m.put(url, json={"max_storage_bytes": 10}).status_code == 403
    assert a.put(url, json={"max_upload_bytes": 10**15}).status_code == 422  # above the platform limit
    assert a.put(url, json={"allowed_upload_types": ["exe"]}).status_code == 422
    assert a.put(url, json={"unknown": 1}).status_code == 422
    png = _png()
    r = a.put(url, json={"allowed_upload_types": ["pdf", "tiff"], "export_retention_days": 7,
                         "note": "Dados alojados no centro de dados da BNP, Lisboa."})
    assert r.status_code == 200 and r.json()["effective"]["export_retention_days"] == 7
    assert r.json()["effective"]["allowed_mime_types"] == ["application/pdf", "image/tiff"]
    rr = _upload(m, p, c, "f.png", png, "image/png")
    assert rr.status_code == 415 and rr.json()["error"]["code"] == "type_not_allowed_by_policy"
    a.put(url, json={"max_upload_bytes": len(png) - 1})
    rr = _upload(m, p, c, "f.png", png, "image/png")
    assert rr.status_code == 413 and rr.json()["error"]["details"]["limit_bytes"] == len(png) - 1
    a.put(url, json={"max_storage_bytes": len(png) * 2 + 10})
    assert _upload(m, p, c, "a.png", png, "image/png").status_code == 201
    assert _upload(m, p, c, "b.png", _png((1, 2, 3)), "image/png").status_code == 201
    rr = _upload(m, p, c, "c.png", _png((9, 9, 9)), "image/png")
    assert rr.status_code == 413 and rr.json()["error"]["code"] == "storage_quota_exceeded"
    # Members see the policy, not usage; administrators see usage computed from the database.
    assert "usage" not in m.get(f"{API}/institutions/{i['id']}/storage").json()
    usage = a.get(f"{API}/institutions/{i['id']}/storage").json()["usage"]
    assert usage["documents"] == 2 and usage["originals_bytes"] == db.execute(text(
        "SELECT sum(a.size_bytes) FROM image_assets a JOIN documents d ON d.source_asset_id=a.id")).scalar()
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action='institution.storage_policy_changed'")).scalar() == 3
    # Independent projects are unaffected by any institution's policy.
    indep = inst["root"]
    ip = indep.post(f"{API}/projects", json={"title": "Own"}).json()
    ic = indep.post(f"{API}/projects/{ip['id']}/collections", json={"title": "x"}).json()
    assert _upload(indep, ip, ic, "z.png", _png((5, 5, 5)), "image/png").status_code == 201


# ------------------------------------------------------------------------------------------ retention

def test_retention_expires_exports_and_removes_unreferenced_data(ws, db):  # noqa: F811
    """Independent project → platform defaults (exports kept 30 days, 7-day grace for unreferenced data)."""
    m, p = ws["pi"], ws["project"]
    c = m.get(f"{API}/projects/{p['id']}/collections").json()[0]
    keep = {"id": ws["page"]["document_id"]}
    gone = _upload(m, p, c, "gone.png", _png((200, 10, 10)), "image/png").json()[0]
    keep_sha = m.get(f"{API}/documents/{keep['id']}").json()["pages"][0]["image"]["sha256"]
    e = m.post(f"{API}/projects/{p['id']}/exports", json={"scope": "document", "scope_id": keep["id"], "formats": ["json"]}).json()
    ej = m.get(f"{API}/exports/{e["id"]}").json()
    assert ej["status"] == "completed", ej.get("error")
    storage_root = Path(get_settings().storage_local_root)
    gone_key = db.execute(text("SELECT a.storage_key FROM image_assets a JOIN documents d ON d.source_asset_id=a.id WHERE d.id=:d"),
                          {"d": gone["id"]}).scalar()
    export_key = db.execute(text("SELECT storage_key FROM exports WHERE id=:e"), {"e": e["id"]}).scalar()
    assert (storage_root / gone_key).is_file() and (storage_root / export_key).is_file()

    assert m.delete(f"{API}/documents/{gone['id']}").status_code == 204
    # Within the grace period nothing is removed, and the export is still within its retention period.
    r = retention.run(db, apply=True)
    assert r["assets_removed"] == 0 and r["objects_removed"] == 0 and r["exports_expired"] == []
    assert (storage_root / gone_key).is_file()

    later = datetime.now(timezone.utc) + timedelta(days=31)  # past the 30-day export retention and the 7-day grace
    dry = retention.run(db, apply=False, now=later)
    assert dry["applied"] is False and [x["id"] for x in dry["exports_expired"]] == [e["id"]] and dry["assets_removed"] == 1
    assert (storage_root / gone_key).is_file()  # dry run changes nothing
    r = retention.run(db, apply=True, now=later)
    # >= 2: the shared test storage root may also hold objects left by earlier tests (their rows were truncated).
    assert r["exports_expired_count"] == 1 and r["assets_removed"] == 1 and r["objects_removed"] >= 2
    assert not (storage_root / gone_key).exists() and not (storage_root / export_key).exists()
    # Referenced research data is untouched and still verifiable.
    page = m.get(f"{API}/documents/{keep['id']}").json()["pages"][0]
    assert page["image"]["sha256"] == keep_sha and m.client.get(page["image"]["url"]).status_code == 200
    ex = m.get(f"{API}/exports/{e['id']}").json()
    assert ex["status"] == "expired"
    d = m.get(f"{API}/exports/{e['id']}/download")
    assert d.status_code == 410 and d.json()["error"]["code"] == "export_expired"
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action='retention.run'")).scalar() == 2
    # Idempotent.
    again = retention.run(db, apply=True, now=later)
    assert again["assets_removed"] == 0 and again["objects_removed"] == 0


def test_retention_admin_endpoint_requires_platform_admin(inst):
    assert inst["admin"].post(f"{API}/admin/retention").status_code == 403
    r = inst["root"].post(f"{API}/admin/retention")
    assert r.status_code == 200 and r.json()["applied"] is False
    st = inst["root"].get(f"{API}/admin/security-status").json()
    assert st["malware_scanner"] == "none" and "secret_key" not in json.dumps({k: v for k, v in st.items() if k != "secret_key_is_default"})


# ------------------------------------------------------------------------------------------ backup / restore

def test_backup_restore_round_trip_is_verified_and_tamper_evident(ws, tmp_path):  # noqa: F811
    """Real pg_dump/pg_restore round trip of the test database into a fresh database."""
    import psycopg

    from app.ops import backup as bk

    s = get_settings()
    src_set = bk.backup(tmp_path / "sets")
    man = json.loads((src_set / "manifest.json").read_text())
    assert man["fingerprint"]["documents"] == 1 and man["objects"] >= 1
    assert bk.verify_set(src_set)["files_verified"] == len((src_set / "SHA256SUMS").read_text().splitlines())

    target = s.database_url.rsplit("/", 1)[0] + "/scriptorium_test_restore"
    admin_uri, env = bk._libpq(s.database_url.rsplit("/", 1)[0] + "/postgres")
    with psycopg.connect(admin_uri, password=env.get("PGPASSWORD"), autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS scriptorium_test_restore")
        c.execute("CREATE DATABASE scriptorium_test_restore")
    rep = bk.restore(src_set, database_url=target, model_dir=tmp_path / "models")
    assert rep["fingerprint_identical"] and rep["assets_verified"] == man["fingerprint"]["image_assets"]
    with pytest.raises(bk.BackupError, match="not empty"):
        bk.restore(src_set, database_url=target, model_dir=tmp_path / "models")
    # Any modified byte in the set is detected before anything is restored.
    victim = next(p for p in (src_set / "objects").rglob("*") if p.is_file())
    data = bytearray(victim.read_bytes())
    data[0] ^= 0xFF
    victim.chmod(0o640)
    victim.write_bytes(bytes(data))
    with pytest.raises(bk.BackupError, match="checksum"):
        bk.verify_set(src_set)


# ------------------------------------------------------------------------------------------ invitations

def test_invited_user_sets_password_when_self_registration_is_closed(inst, client, outbox, monkeypatch, db):
    import re

    from tests.test_auth import _mail

    monkeypatch.setattr(get_settings(), "allow_self_registration", False)
    assert client.post(f"{API}/auth/register", json={"email": "x@bnp.pt", "full_name": "X",
                                                     "password": "Correct-Horse-Battery-9"}).status_code == 403
    a, m, i = inst["admin"], inst["member"], inst["inst"]
    assert m.post(f"{API}/admin/users", json={"email": "new@bnp.pt", "full_name": "Nova", "institution_id": i["id"]}).status_code == 403
    assert a.post(f"{API}/admin/users", json={"email": "new@bnp.pt", "full_name": "Nova"}).status_code == 403  # no institution → platform admin only
    r = a.post(f"{API}/admin/users", json={"email": "new@bnp.pt", "full_name": "Nova Investigadora", "institution_id": i["id"]})
    assert r.status_code == 201, r.text
    assert a.post(f"{API}/admin/users", json={"email": "NEW@bnp.pt", "full_name": "dup", "institution_id": i["id"]}).status_code == 409
    msg, body = _mail(next(outbox.glob("*.eml")))
    assert msg["To"] == "new@bnp.pt" and "7 days" in body
    token = re.search(r"token=([\w-]+)", body).group(1)
    # No usable password before the invitation is accepted.
    assert client.post(f"{API}/auth/login", json={"email": "new@bnp.pt", "password": "anything-at-all-123"}).status_code == 401
    assert client.post(f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": "Chosen-Passphrase-77"}).status_code == 204
    tok = client.post(f"{API}/auth/login", json={"email": "new@bnp.pt", "password": "Chosen-Passphrase-77"}).json()["access_token"]
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert [x["institution_id"] for x in me["institutions"]] == [i["id"]]
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action='admin.user_invited'")).scalar() == 1
