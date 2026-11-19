#!/usr/bin/env python3
"""Kill a real Celery worker (SIGKILL) during real Kraken recognition and verify recovery.

Requires the dev stack (scripts/dev-up.sh) with TraPrInq registered, and the sample page.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VENV = Path(os.environ.get("VENV", ROOT / ".venv"))
API = "http://127.0.0.1:8000/api/v1"
SAMPLE = ROOT / "var/samples/traprinq-val/PT-TT-TSO-IL-28-7926_m0052.jpg"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def start_worker() -> subprocess.Popen:
    env = {**os.environ, "SCRIPTORIUM_JOB_STALE_AFTER_SECONDS": "30"}
    return subprocess.Popen([str(VENV / "bin/celery"), "-A", "app.worker.celery_app", "worker", "-Q", "default,recognition,models",
                             "-c", "1", "-n", f"crashtest-{uuid.uuid4().hex[:4]}@%h", "--loglevel", "WARNING"],
                            cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def main() -> int:
    subprocess.run("pkill -f 'celery -A app.worker.celery_app' || true", shell=True)
    time.sleep(2)
    c = httpx.Client(base_url=API, timeout=60)
    email, pw = f"crash-{uuid.uuid4().hex[:6]}@example.org", "Crash-Recovery-2026"
    c.post("/auth/register", json={"email": email, "full_name": "Crash Test", "password": pw})
    h = {"Authorization": "Bearer " + c.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]}
    p = c.post("/projects", headers=h, json={"title": "Worker crash recovery"}).json()
    col = c.post(f"/projects/{p['id']}/collections", headers=h, json={"title": "C"}).json()
    worker = start_worker()
    d = c.post(f"/projects/{p['id']}/collections/{col['id']}/documents", headers=h,
               files=[("files", (SAMPLE.name, SAMPLE.read_bytes(), "image/jpeg"))]).json()[0]
    for _ in range(60):
        doc = c.get(f"/documents/{d['id']}", headers=h).json()
        if doc["ingest_status"] == "ready":
            break
        time.sleep(1)
    models = c.get("/models?usable=true&task=recognition", headers=h).json()
    trap = next(m for m in models if m["model_slug"] == "traprinq")
    job = c.post(f"/projects/{p['id']}/recognition-jobs", headers=h,
                 json={"page_ids": [doc["pages"][0]["id"]], "recognition_model_version_id": trap["id"]}).json()["jobs"][0]
    log(f"job {job['id']} submitted")
    while True:
        j = c.get(f"/recognition-jobs/{job['id']}", headers=h).json()
        if j["stage"] == "recognition":
            break
        time.sleep(1)
    log(f"job is in stage '{j['stage']}' on worker {j['worker']} (attempt {j['attempts']}) — sending SIGKILL to the worker")
    os.killpg(worker.pid, signal.SIGKILL)
    worker.wait()
    time.sleep(35)
    j = c.get(f"/recognition-jobs/{job['id']}", headers=h).json()
    log(f"after kill: status={j['status']} stage={j['stage']} (orphaned, no heartbeat)")
    log("starting a new worker (startup recovery)")
    worker = start_worker()
    t0 = time.time()
    while time.time() - t0 < 600:
        j = c.get(f"/recognition-jobs/{job['id']}", headers=h).json()
        if j["status"] in ("completed", "failed"):
            break
        time.sleep(3)
    os.killpg(worker.pid, signal.SIGTERM)
    rid = j["output_recognition_id"]
    lines = c.get(f"/recognitions/{rid}", headers=h).json()["line_count"] if rid else None
    log(f"final: status={j['status']} attempts={j['attempts']} recognised_lines={lines}")
    log("job log: " + json.dumps([e["message"] for e in j["log"]], ensure_ascii=False))
    ok = j["status"] == "completed" and j["attempts"] == 2 and any("re-queued" in e["message"] for e in j["log"])
    segs = c.get(f"/pages/{doc['pages'][0]['id']}/segmentations", headers=h).json()
    log(f"segmentations stored for the page: {len(segs)} (no partial output from the killed attempt)")
    ok = ok and len(segs) == 1
    log("RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
