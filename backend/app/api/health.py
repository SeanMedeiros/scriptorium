from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.db import get_engine
from app.services.throttle import get_redis
from app.storage import get_storage

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live():
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
def ready():
    checks: dict[str, dict] = {}

    def run(name, fn):
        t = time.perf_counter()
        try:
            detail = fn()
            checks[name] = {"status": "ok", "ms": round((time.perf_counter() - t) * 1000, 1), **(detail or {})}
        except Exception as e:  # noqa: BLE001 — health endpoint reports any failure
            checks[name] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300]}

    def db():
        with get_engine().connect() as c:
            rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return {"schema_revision": rev}

    def redis():
        get_redis().ping()

    def storage():
        s = get_storage()
        s.healthcheck()
        return {"backend": s.name}

    def malware_scanner():
        from app.config import get_settings
        from app.services import malware

        st = get_settings()
        if st.malware_scanner == "none":
            return {"scanner": "disabled"}
        return {"scanner": "clamd", "version": malware.ping()}

    run("database", db)
    run("redis", redis)
    run("storage", storage)
    run("malware_scanner", malware_scanner)
    ok = all(c["status"] == "ok" for c in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "version": __version__, "checks": checks},
                        status_code=200 if ok else 503)
