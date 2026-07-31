"""Persistent-entity definitions.

Importing this package registers every SQLModel-backed table with
``SQLModel.metadata`` so Alembic autogen + ``Database.create_all``
both see them.
"""

from __future__ import annotations

from voicegateway.models.agent_observation_model import AgentObservation
from voicegateway.models.agent_probe_result_model import AgentProbeResult
from voicegateway.models.api_key_model import ApiKey
from voicegateway.models.base_model import BaseModel, BaseUUIDModel
from voicegateway.models.call_leg_model import CallLeg
from voicegateway.models.call_model import Call
from voicegateway.models.config_audit_log_model import ConfigAuditLog
from voicegateway.models.dead_air_event_model import DeadAirEvent
from voicegateway.models.diagnostics_run_model import DiagnosticsRun
from voicegateway.models.latency_observation_model import LatencyObservation
from voicegateway.models.managed_model_model import ManagedModel
from voicegateway.models.managed_project_model import ManagedProject
from voicegateway.models.managed_provider_model import ManagedProvider
from voicegateway.models.managed_rate_rule_model import ManagedRateRule
from voicegateway.models.node_sample_model import NodeSample
from voicegateway.models.replay_event_model import (
    ReplayLlmToken,
    ReplayStateSnapshot,
    ReplaySttEvent,
    ReplayTtsFrame,
)
from voicegateway.models.request_model import Request, RequestRecord
from voicegateway.models.session_model import Session
from voicegateway.models.tenant_model import Tenant
from voicegateway.models.transcript_turn_model import TranscriptTurn
from voicegateway.models.turn_model import Turn
from voicegateway.models.worker_model import Worker

__all__ = [
    "AgentObservation",
    "AgentProbeResult",
    "BaseModel",
    "BaseUUIDModel",
    "Call",
    "CallLeg",
    "ConfigAuditLog",
    "DeadAirEvent",
    "DiagnosticsRun",
    "LatencyObservation",
    "ManagedModel",
    "ManagedProject",
    "ManagedProvider",
    "ManagedRateRule",
    "NodeSample",
    "ReplayLlmToken",
    "ReplaySttEvent",
    "ReplayStateSnapshot",
    "ReplayTtsFrame",
    "Request",
    "RequestRecord",
    "Session",
    "Tenant",
    "TranscriptTurn",
    "Turn",
    "Worker",
    "ApiKey",
]
