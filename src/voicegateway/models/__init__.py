"""Persistent-entity definitions."""

from __future__ import annotations

from voicegateway.models.base import BaseModel, BaseUUIDModel
from voicegateway.models.request import RequestRecord
from voicegateway.models.virtual_key import VirtualKey

__all__ = [
    "BaseModel",
    "BaseUUIDModel",
    "RequestRecord",
    "VirtualKey",
]
