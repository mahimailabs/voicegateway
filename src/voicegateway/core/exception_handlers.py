"""FastAPI exception handlers that emit a uniform JSON error envelope.

Every error response carries the same keys: ``message`` (human-friendly),
``error`` (raw detail), ``type`` (exception class name), and an optional
``details`` / ``errors`` payload. Frontends can rely on the shape; the
HTTP status code carries the semantics.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from voicegateway.core.exceptions import (
    AuthError,
    DuplicatedError,
    NotFoundError,
    NotSatisfiableError,
    PermissionDeniedError,
    UnauthorizedError,
    ValidationError,
)


def create_error_response(
    status_code: int,
    message: str,
    error: str,
    error_type: str,
    details: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Render the canonical error envelope."""
    content: dict[str, Any] = {
        "message": message,
        "error": error,
        "type": error_type,
    }
    if details:
        content["details"] = details
    if errors:
        content["errors"] = errors
    return JSONResponse(status_code=status_code, content=content)


def _request_meta(request: Request) -> dict[str, Any]:
    return {"path": request.url.path, "method": request.method}


def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.error(f"Request validation error: {exc.errors()}")
    errors = [
        {
            "field": ".".join(str(x) for x in err["loc"]),
            "message": err["msg"],
            "type": err["type"],
            "input_value": err.get("input"),
        }
        for err in exc.errors()
    ]
    return create_error_response(
        status_code=422,
        message="Request validation failed",
        error="Invalid request data",
        error_type="RequestValidationError",
        errors=errors,
    )


def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"Unexpected error occurred: {exc}")
    return create_error_response(
        status_code=500,
        message="An unexpected error occurred",
        error=str(exc),
        error_type=exc.__class__.__name__,
        details=_request_meta(request),
    )


def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    logger.error(f"Database error occurred: {exc}")
    return create_error_response(
        status_code=500,
        message="A database error occurred",
        error=str(exc),
        error_type="DatabaseError",
        details=_request_meta(request),
    )


def duplicated_error_handler(request: Request, exc: DuplicatedError) -> JSONResponse:
    return create_error_response(
        status_code=400,
        message="Duplicate entry",
        error=str(exc.detail),
        error_type="DuplicatedError",
        details=_request_meta(request),
    )


def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    return create_error_response(
        status_code=403,
        message="Authentication failed",
        error=str(exc.detail),
        error_type="AuthError",
        details=_request_meta(request),
    )


def not_found_error_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    logger.error(f"Not found: {exc.detail}")
    return create_error_response(
        status_code=404,
        message="Resource not found",
        error=str(exc.detail),
        error_type="NotFoundError",
        details=_request_meta(request),
    )


def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
    logger.error(f"Validation error: {exc.detail}")
    return create_error_response(
        status_code=422,
        message="Validation failed",
        error=str(exc.detail),
        error_type="ValidationError",
        details=_request_meta(request),
    )


def permission_denied_error_handler(
    request: Request, exc: PermissionDeniedError
) -> JSONResponse:
    return create_error_response(
        status_code=403,
        message="Permission denied",
        error=str(exc.detail),
        error_type="PermissionDeniedError",
        details=_request_meta(request),
    )


def unauthorized_error_handler(
    request: Request, exc: UnauthorizedError
) -> JSONResponse:
    return create_error_response(
        status_code=401,
        message="Unauthorized",
        error=str(exc.detail),
        error_type="UnauthorizedError",
        details=_request_meta(request),
    )


def not_satisfiable_error_handler(
    request: Request, exc: NotSatisfiableError
) -> JSONResponse:
    return create_error_response(
        status_code=416,
        message="Not satisfiable",
        error=str(exc.detail),
        error_type="NotSatisfiableError",
        details=_request_meta(request),
    )
