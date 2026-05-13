"""Persistent-entity definitions."""

from __future__ import annotations

from voicegateway.models.base_model import BaseModel, BaseUUIDModel
from voicegateway.models.request_model import RequestRecord
from voicegateway.models.virtual_key_model import VirtualKey

__all__ = [
    "BaseModel",
    "BaseUUIDModel",
    "RequestRecord",
    "VirtualKey",
]
