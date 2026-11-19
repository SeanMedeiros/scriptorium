#!/usr/bin/env python3
"""Acceptance criterion Q: the application restarts without losing persistent data.

Creates data through the real API, restarts PostgreSQL, Redis and the API process,
then verifies every record and stored object through the API and on disk.
Native-service variant (used in the build environment); for Docker use
`docker compose restart` and run with --no-service-restart.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV = Path(os.environ.get("VENV", ROOT / ".venv"))
API = "http://127.0.0.1:8000"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def start_api() -> subprocess.Popen:
    p = subprocess.Popen([str(VENV / "bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                         cwd=BACKEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if httpx.get(f"{API}/api/health/ready", timeout=2).status_code == 200:
                return p
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("API did not become ready")


def stop_port_8000() -> None:
    subprocess.run("pkill -f 'uvicorn app.main:app --host 127.0.0.1 --port 8000' || true", shell=True)
    time.sleep(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-service-restart", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)

    stop_port_8000()
    api_proc = start_api()
    c = httpx.Client(base_url=f"{API}/api/v1", timeout=30)
    email = f"persist-{uuid.uuid4().hex[:8]}@persistence.example.org"
    pw = "Persistence-Check-2026"
    assert c.post("/auth/register", json={"email": email, "full_name": "Persistence Check", "password": pw}).status_code == 201
    tok = c.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    proj = c.post("/projects", headers=h, json={"title": "Persistence verification", "languages": ["por"],
                                                "period_start": 1700, "period_end": 1750}).json()
    coll = c.post(f"/projects/{proj['id']}/collections", headers=h,
                  json={"title": "Livro de notas 12", "archival_reference": "PT/ADX/NOT/12",
                        "dc_metadata": {"date": "1712/1715"}}).json()
    audit_before = c.get("/audit-logs", headers=h, params={"project_id": proj["id"]}).json()["total"]

    from app.storage import get_storage  # uses SCRIPTORIUM_* from backend/.env

    payload = os.urandom(256 * 1024)
    obj = get_storage().put_stream("persistence-check", io.BytesIO(payload))
    log(f"created user={email} project={proj['id']} collection={coll['id']} audit_entries={audit_before}")
    log(f"stored object {obj.key} sha256={obj.sha256}")

    log("stopping API process")
    api_proc.terminate()
    api_proc.wait(10)
    stop_port_8000()
    if not args.no_service_restart:
        log("restarting PostgreSQL 16 cluster")
        subprocess.run(["pg_ctlcluster", "16", "main", "restart"], check=True)
        log("restarting Redis (SHUTDOWN SAVE, start with AOF)")
        subprocess.run(["redis-cli", "shutdown", "save"], check=False)
        time.sleep(1)
        subprocess.run(["redis-server", "--daemonize", "yes", "--appendonly", "yes", "--dir", "/var/lib/redis"], check=True)
        time.sleep(1)
    log("starting API process")
    api_proc = start_api()

    c2 = httpx.Client(base_url=f"{API}/api/v1", timeout=30)
    r = c2.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    p2 = c2.get(f"/projects/{proj['id']}", headers=h2).json()
    assert (p2["title"], p2["languages"], p2["period_start"]) == ("Persistence verification", ["por"], 1700), p2
    c2_ = c2.get(f"/projects/{proj['id']}/collections/{coll['id']}", headers=h2).json()
    assert c2_["archival_reference"] == "PT/ADX/NOT/12" and c2_["dc_metadata"] == {"date": "1712/1715"}, c2_
    audit_after = c2.get("/audit-logs", headers=h2, params={"project_id": proj["id"]}).json()["total"]
    assert audit_after == audit_before, (audit_before, audit_after)
    got = b"".join(get_storage().iter_bytes(obj.key))
    assert hashlib.sha256(got).hexdigest() == obj.sha256
    ready = httpx.get(f"{API}/api/health/ready").json()
    log(f"after restart: login OK, project OK, collection OK, audit_entries={audit_after}, object sha256 OK")
    log(f"health: {ready['status']} schema={ready['checks']['database']['schema_revision']}")
    log("RESULT: PASS — no persistent data lost across restart")
    api_proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
