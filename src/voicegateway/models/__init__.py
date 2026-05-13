"""Persistent-entity definitions.

Imports every SQLModel-mapped entity at package load so
``SQLModel.metadata`` is populated for Alembic autogenerate. Plain
dataclass entities (e.g. :class:`voicegateway.models.request.RequestRecord`)
do not need to be eagerly imported but are surfaced here for convenience.
"""

from __future__ import annotations

from voicegateway.models.base import BaseModel, BaseUUIDModel
from voicegateway.models.request import RequestRecord

__all__ = [
    "BaseModel",
    "BaseUUIDModel",
    "RequestRecord",
]
