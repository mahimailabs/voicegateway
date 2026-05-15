"""SQLAlchemy + dependency-injector wiring for the FastAPI app.

Lives alongside :mod:`voicegateway.core.container` and
:mod:`voicegateway.core.exception_handlers` because everything this
module wires onto the FastAPI app is itself in ``voicegateway.core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dependency_injector import providers
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

from voicegateway.core.container import Container
from voicegateway.core.exception_handlers import (
    auth_error_handler,
    duplicated_error_handler,
    global_exception_handler,
    not_found_error_handler,
    not_satisfiable_error_handler,
    permission_denied_error_handler,
    request_validation_exception_handler,
    sqlalchemy_exception_handler,
    unauthorized_error_handler,
    validation_error_handler,
)
from voicegateway.core.exceptions import (
    AuthError as LayeredAuthError,
)
from voicegateway.core.exceptions import (
    DuplicatedError,
    NotFoundError,
    NotSatisfiableError,
    PermissionDeniedError,
    UnauthorizedError,
)
from voicegateway.core.exceptions import (
    ValidationError as LayeredValidationError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from voicegateway.core.gateway import Gateway


def attach_layered_stack(app: FastAPI, gateway: Gateway) -> None:
    """Wire the SQLAlchemy + dependency-injector layer onto the FastAPI app."""
    container = Container()
    container.config.override(providers.Object(gateway.config))
    container.wire(modules=container.wiring_config.modules)
    app.state.container = container

    app.add_exception_handler(
        RequestValidationError, request_validation_exception_handler
    )
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    for exc_class, handler in (
        (DuplicatedError, duplicated_error_handler),
        (LayeredAuthError, auth_error_handler),
        (NotFoundError, not_found_error_handler),
        (LayeredValidationError, validation_error_handler),
        (PermissionDeniedError, permission_denied_error_handler),
        (UnauthorizedError, unauthorized_error_handler),
        (NotSatisfiableError, not_satisfiable_error_handler),
    ):
        app.add_exception_handler(exc_class, handler)
    app.add_exception_handler(Exception, global_exception_handler)


__all__ = ["attach_layered_stack"]
