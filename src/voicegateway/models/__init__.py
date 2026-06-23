"""Persistent-entity definitions.

Importing this package registers every SQLModel-backed table with
``SQLModel.metadata`` so Alembic autogen + ``Database.create_all``
both see them.
"""

from __future__ import annotations

from voicegateway.models.agent_observation_model import AgentObservation
from voicegateway.models.base_model import BaseModel, BaseUUIDModel
from voicegateway.models.config_audit_log_model import ConfigAuditLog
from voicegateway.models.dead_air_event_model import DeadAirEvent
from voicegateway.models.guardrail_event_model import GuardrailEvent
from voicegateway.models.latency_observation_model import LatencyObservation
from voicegateway.models.managed_model_model import ManagedModel
from voicegateway.models.managed_project_model import ManagedProject
from voicegateway.models.managed_provider_model import ManagedProvider
from voicegateway.models.replay_event_model import (
    ReplayLlmToken,
    ReplayStateSnapshot,
    ReplaySttEvent,
    ReplayTtsFrame,
)
from voicegateway.models.request_model import Request, RequestRecord
from voicegateway.models.session_model import Session
from voicegateway.models.turn_model import Turn
from voicegateway.models.api_key_model import ApiKey

__all__ = [
    "AgentObservation",
    "BaseModel",
    "BaseUUIDModel",
    "ConfigAuditLog",
    "DeadAirEvent",
    "GuardrailEvent",
    "LatencyObservation",
    "ManagedModel",
    "ManagedProject",
    "ManagedProvider",
    "ReplayLlmToken",
    "ReplaySttEvent",
    "ReplayStateSnapshot",
    "ReplayTtsFrame",
    "Request",
    "RequestRecord",
    "Session",
    "Turn",
    "ApiKey",
]
