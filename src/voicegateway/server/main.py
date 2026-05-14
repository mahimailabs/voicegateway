"""VoiceGateway HTTP API server."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from voicegateway.core.auth import load_api_keys, resolve_cors_origins
from voicegateway.server.routes import api_router, system_router

if TYPE_CHECKING:
    from voicegateway.core.gateway import Gateway

logger = logging.getLogger(__name__)


def _attach_layered_stack(app: FastAPI, gateway: Gateway) -> None:
    """Wire the SQLAlchemy + dependency-injector layer onto the FastAPI app."""
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


def build_app(gateway: Gateway) -> FastAPI:
    """Build a FastAPI app bound to the given Gateway instance."""
    app = FastAPI(
        title="VoiceGateway API",
        version="0.6.0",
        description=(
            "HTTP API for VoiceGateway: cost tracking and reconciliation "
            "for LiveKit voice agents."
        ),
    )

    _attach_layered_stack(app, gateway)

    api_keys = load_api_keys(gateway.config.auth)
    cors_origins = resolve_cors_origins(gateway.config.auth)
    if cors_origins == ["*"]:
        logger.warning(
            "CORS: allow_origins=['*']. Set auth.cors_origins in "
            "voicegw.yaml to restrict."
        )

    # Bind app-wide state so ``get_gateway`` and ``require_scope`` (in
    # ``server.api._deps``) read gateway + api_keys without closure capture.
    app.state.gateway = gateway
    app.state.api_keys = api_keys
    app.state.started_at = time.time()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Deprecation"],
    )

    app.include_router(system_router)
    app.include_router(api_router)

    return app
