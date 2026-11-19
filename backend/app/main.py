"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (admin, auth, comparison, dashboard, documents, evaluation, exports, health, institutions, interop, projects, recognition,
                     registry, reports, training, transcriptions, users)
from app.config import get_settings
from app.core.errors import install_error_handlers
from app.logging_setup import configure_logging, request_id_var, user_id_var

log = logging.getLogger("scriptorium.http")


# The API returns JSON/binary only, so the strictest CSP applies. The interactive API documentation
# pages (/api/docs, /api/redoc) use self-hosted bundles that need inline bootstrap scripts.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")


def _security_headers(path: str, headers, hsts_seconds: int) -> None:
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    headers.setdefault("Content-Security-Policy", _DOCS_CSP if path in ("/api/docs", "/api/redoc") else _API_CSP)
    if path.startswith("/api/v1/auth") or path.startswith("/api/v1/users/me"):
        headers.setdefault("Cache-Control", "no-store")
    if hsts_seconds > 0:
        headers.setdefault("Strict-Transport-Security", f"max-age={hsts_seconds}; includeSubDomains")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    app = FastAPI(
        title="SCRIPTORIUM API",
        version=__version__,
        description="Historical Document Intelligence Platform — REST API. "
        "All recognition runs on institution-controlled Kraken infrastructure; no document is sent to third-party AI services.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request_id_var.set(rid[:64])
        user_id_var.set(None)
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            status = response.status_code if response is not None else 500
            if response is not None:
                response.headers["X-Request-ID"] = rid[:64]
                _security_headers(request.url.path, response.headers, s.hsts_seconds)
            if not request.url.path.startswith("/api/health"):
                log.info("request", extra={"method": request.method, "path": request.url.path, "status": status, "ms": ms})

    api = APIRouter(prefix="/api/v1")
    for r in (auth.router, users.router, institutions.router, projects.router, documents.router, recognition.router,
              registry.router, transcriptions.router, evaluation.router, comparison.router, exports.router,
              reports.router, training.router, interop.router, dashboard.router, admin.router):
        api.include_router(r)
    app.include_router(api)
    app.include_router(health.router, prefix="/api")
    _mount_api_docs(app)
    return app


def _mount_api_docs(app: FastAPI) -> None:
    """Interactive API documentation served from vendored assets (no third-party CDN)."""
    from pathlib import Path

    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
    from fastapi.staticfiles import StaticFiles

    static = Path(__file__).parent / "static" / "apidocs"
    app.mount("/api/static/apidocs", StaticFiles(directory=static), name="apidocs")

    @app.get("/api/docs", include_in_schema=False)
    def swagger():
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="SCRIPTORIUM API",
                                   swagger_js_url="/api/static/apidocs/swagger-ui-bundle.js",
                                   swagger_css_url="/api/static/apidocs/swagger-ui.css",
                                   swagger_favicon_url="/api/static/apidocs/favicon-32x32.png")

    @app.get("/api/redoc", include_in_schema=False)
    def redoc():
        return get_redoc_html(openapi_url="/api/openapi.json", title="SCRIPTORIUM API",
                              redoc_js_url="/api/static/apidocs/redoc.standalone.js",
                              redoc_favicon_url="/api/static/apidocs/favicon-32x32.png", with_google_fonts=False)


app = create_app()
