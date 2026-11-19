"""Structured error responses: {"error": {"code", "message", "details", "request_id"}}."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_setup import request_id_var


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict | None = None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def not_found(entity: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{entity} not found")


def forbidden(message: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, "forbidden", message)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code, headers=getattr(exc, "headers", None))
