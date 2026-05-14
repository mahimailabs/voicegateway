"""Router aggregator for the VoiceGateway HTTP API.

Two routers compose every endpoint:

- ``system_router`` is mounted at the app root because ``/health`` does
  not carry the ``/v1`` prefix.
- ``api_router`` carries the ``/v1`` prefix and is the parent of every
  domain router under :mod:`voicegateway.server.api`.

Commit 1 leaves both routers empty. Commit 2 registers every domain
``include_router`` call below.
"""

from __future__ import annotations

from fastapi import APIRouter

system_router = APIRouter()
api_router = APIRouter(prefix="/v1")


__all__ = ["api_router", "system_router"]
