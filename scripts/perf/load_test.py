#!/usr/bin/env python3
"""HTTP load test of the SCRIPTORIUM API (performance evidence, Phase 7).

    load_test.py --base http://127.0.0.1:8003 --email … --password … --users 20 --seconds 60

Each virtual user loops over a realistic researcher mix (weights in MIX): dashboard, project and
document listings (the first collection page of a large collection), document detail, transcription
load, a page image through its signed URL, audit log, and — 10 % of iterations — a real transcription
revision (optimistic locking: concurrent edits of the same line legitimately answer 409, counted
separately). Reports throughput and p50/p95/p99 latency per operation; errors are any non-2xx except
those 409s. Run against a COPY of an installation, never production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from collections import defaultdict

import httpx

MIX = [("dashboard", 15), ("projects", 10), ("documents_page", 15), ("document", 15), ("transcription", 15),
       ("page_image", 10), ("audit", 10), ("save_revision", 10)]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1) if xs else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--out")
    a = ap.parse_args()

    async with httpx.AsyncClient(base_url=a.base, timeout=60) as c:
        tok = (await c.post("/api/v1/auth/login", json={"email": a.email, "password": a.password})).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        projects = (await c.get("/api/v1/projects", headers=h)).json()
        p = (projects if isinstance(projects, list) else projects["items"])[0]
        col = (await c.get(f"/api/v1/projects/{p['id']}/collections", headers=h)).json()[0]
        docs = (await c.get(f"/api/v1/projects/{p['id']}/collections/{col['id']}/documents?limit=500", headers=h)).json()
        total_docs = docs.get("total") or len(docs["items"])
        tail = (await c.get(f"/api/v1/projects/{p['id']}/collections/{col['id']}/documents",
                            params={"limit": 500, "offset": max(0, total_docs - 500)}, headers=h)).json()
        real_docs = [d for d in docs["items"] + tail["items"] if not d["title"].startswith("PERF-")]
        real_docs = list({d["id"]: d for d in real_docs}.values())
        detail = [(await c.get(f"/api/v1/documents/{d['id']}", headers=h)).json() for d in real_docs]
        pages = [pg for d in detail for pg in d["pages"]]
        txs = []
        for pg in pages:
            txs += [t["id"] for t in (await c.get(f"/api/v1/pages/{pg['id']}/transcriptions", headers=h)).json()]
    print(f"dataset: {total_docs} documents in the collection, {len(real_docs)} with real images, {len(pages)} real pages, "
          f"{len(txs)} transcriptions; {a.users} virtual users × {a.seconds}s")

    lat: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    conflicts = 0
    image_bytes = 0
    names, weights = zip(*MIX)
    deadline = time.monotonic() + a.seconds

    async def vu(i: int) -> None:
        nonlocal conflicts, image_bytes
        rnd = random.Random(i)
        async with httpx.AsyncClient(base_url=a.base, timeout=60, headers=h) as c:
            while time.monotonic() < deadline:
                op = rnd.choices(names, weights)[0]
                t0 = time.perf_counter()
                if op == "dashboard":
                    r = await c.get("/api/v1/dashboard")
                elif op == "projects":
                    r = await c.get("/api/v1/projects")
                elif op == "documents_page":
                    r = await c.get(f"/api/v1/projects/{p['id']}/collections/{col['id']}/documents",
                                    params={"limit": 100, "offset": rnd.randrange(0, max(1, (total_docs or 100) - 100))})
                elif op == "document":
                    r = await c.get(f"/api/v1/documents/{rnd.choice(real_docs)['id']}")
                elif op == "transcription":
                    r = await c.get(f"/api/v1/transcriptions/{rnd.choice(txs)}")
                elif op == "page_image":
                    pg = rnd.choice(pages)
                    fresh = (await c.get(f"/api/v1/pages/{pg['id']}")).json()["image"]["url"]
                    t0 = time.perf_counter()  # measure the image transfer itself
                    r = await c.get(fresh)
                    image_bytes += len(r.content)
                elif op == "audit":
                    r = await c.get("/api/v1/audit-logs", params={"limit": 50})
                else:  # save_revision
                    tid = rnd.choice(txs)
                    t = (await c.get(f"/api/v1/transcriptions/{tid}")).json()
                    line = rnd.choice(t["lines"])
                    t0 = time.perf_counter()
                    cur = line["current"]
                    text = (cur.get("text") or "").rstrip(" ·") + (" ·" if rnd.random() < 0.5 else "")
                    r = await c.post(f"/api/v1/transcriptions/{tid}/revisions", json={
                        "message": "load test", "changes": [{"line_id": line["line_id"], "expected_version_id": cur["version_id"],
                                                             "text": text}]})
                    if r.status_code == 409:
                        conflicts += 1
                        continue
                ms = (time.perf_counter() - t0) * 1000
                if r.status_code >= 300:
                    errors[op] += 1
                else:
                    lat[op].append(ms)

    t_start = time.monotonic()
    await asyncio.gather(*(vu(i) for i in range(a.users)))
    elapsed = time.monotonic() - t_start
    allv = [x for v in lat.values() for x in v]
    res = {
        "virtual_users": a.users, "seconds": round(elapsed, 1), "requests_ok": len(allv),
        "throughput_rps": round(len(allv) / elapsed, 1), "errors": dict(errors), "edit_conflicts_409": conflicts,
        "image_mb_served": round(image_bytes / 1e6, 1),
        "overall_ms": {"p50": pct(allv, 50), "p95": pct(allv, 95), "p99": pct(allv, 99)},
        "by_operation": {k: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99),
                             "mean": round(statistics.mean(v), 1)} for k, v in sorted(lat.items())},
    }
    print(json.dumps(res, indent=2))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
