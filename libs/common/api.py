from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.common.config import ensure_base_dirs


def success_response(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_payload(message: str, code: str = "error", details: Any = None) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message, "details": details}}


def create_app(title: str, enable_cors: bool = False) -> FastAPI:
    ensure_base_dirs()
    app = FastAPI(title=title, version="1.0.0")

    if enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "message" in detail:
            payload = error_payload(
                message=detail["message"],
                code=detail.get("code", "http_error"),
                details=detail.get("details"),
            )
        else:
            payload = error_payload(message=str(detail), code="http_error")
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def generic_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content=error_payload(message=str(exc), code="internal_error"))

    return app


def fail(message: str, status_code: int = 400, code: str = "bad_request", details: Any = None) -> None:
    raise HTTPException(status_code=status_code, detail={"message": message, "code": code, "details": details})
