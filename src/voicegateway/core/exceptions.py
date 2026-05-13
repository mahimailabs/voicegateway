"""HTTP-shaped exception types raised by services/repositories.

Each subclass binds a single status code so call sites stay terse:
``raise NotFoundError("session not found")``. The matching JSON shape
is rendered by :mod:`voicegateway.core.exception_handlers`.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def create_credential_exception(detail: str) -> HTTPException:
    """401 with the WWW-Authenticate header that browsers expect."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_service_unavailable_exception(detail: str) -> HTTPException:
    """503 for "feature configured off / dependency missing" branches."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


class DuplicatedError(HTTPException):
    """400 — unique-constraint violation surfaced at the service layer."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, detail, headers)


class AuthError(HTTPException):
    """403 — caller authenticated but lacks rights for the action."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, detail, headers)


class NotFoundError(HTTPException):
    """404 — requested resource does not exist."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, detail, headers)


class ValidationError(HTTPException):
    """422 — request payload failed domain validation past pydantic."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, detail, headers)


class PermissionDeniedError(HTTPException):
    """403 — same status as :class:`AuthError`, distinguished by intent."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, detail, headers)


class UnauthorizedError(HTTPException):
    """401 — token missing or invalid."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail, headers)


class NotSatisfiableError(HTTPException):
    """416 — range/precondition request the caller cannot have served."""

    def __init__(
        self, detail: Any = None, headers: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, detail, headers
        )
