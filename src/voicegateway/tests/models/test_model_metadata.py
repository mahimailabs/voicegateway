"""Verify every legacy table is registered as a SQLModel."""

from __future__ import annotations

from dataclasses import is_dataclass

import pytest
from sqlmodel import SQLModel

import voicegateway.models  # noqa: F401 — registers every model

_EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "config_audit_log",
        "dead_air_events",
        "guardrail_events",
        "latency_observations",
        "managed_models",
        "managed_projects",
        "managed_providers",
        "replay_llm_tokens",
        "replay_state_snapshots",
        "replay_stt_events",
        "replay_tts_frames",
        "requests",
        "sessions",
        "turns",
        "api_keys",
    }
)


def test_every_expected_table_registered() -> None:
    registered = set(SQLModel.metadata.tables.keys())
    missing = _EXPECTED_TABLES - registered
    assert not missing, (
        f"tables not registered with SQLModel.metadata: {sorted(missing)}"
    )


def test_request_record_dataclass_preserved() -> None:
    """Producers still use RequestRecord as a dataclass; do not break that."""
    from voicegateway.models.request_model import RequestRecord

    assert is_dataclass(RequestRecord)
    rec = RequestRecord(
        id="req-1",
        timestamp=1700000000.0,
        modality="llm",
        model_id="openai/gpt-4",
        provider="openai",
        metadata={"foo": "bar"},
    )
    assert rec.metadata == {"foo": "bar"}
    assert rec.project == "default"


@pytest.mark.parametrize(
    ("model_path", "expected_table"),
    [
        (
            "voicegateway.models.config_audit_log_model:ConfigAuditLog",
            "config_audit_log",
        ),
        ("voicegateway.models.dead_air_event_model:DeadAirEvent", "dead_air_events"),
        (
            "voicegateway.models.guardrail_event_model:GuardrailEvent",
            "guardrail_events",
        ),
        (
            "voicegateway.models.latency_observation_model:LatencyObservation",
            "latency_observations",
        ),
        ("voicegateway.models.managed_model_model:ManagedModel", "managed_models"),
        (
            "voicegateway.models.managed_project_model:ManagedProject",
            "managed_projects",
        ),
        (
            "voicegateway.models.managed_provider_model:ManagedProvider",
            "managed_providers",
        ),
        ("voicegateway.models.replay_event_model:ReplayLlmToken", "replay_llm_tokens"),
        (
            "voicegateway.models.replay_event_model:ReplayStateSnapshot",
            "replay_state_snapshots",
        ),
        ("voicegateway.models.replay_event_model:ReplaySttEvent", "replay_stt_events"),
        ("voicegateway.models.replay_event_model:ReplayTtsFrame", "replay_tts_frames"),
        ("voicegateway.models.request_model:Request", "requests"),
        ("voicegateway.models.session_model:Session", "sessions"),
        ("voicegateway.models.turn_model:Turn", "turns"),
    ],
)
def test_model_class_maps_to_table(model_path: str, expected_table: str) -> None:
    module_path, _, class_name = model_path.partition(":")
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert cls.__tablename__ == expected_table
    assert expected_table in SQLModel.metadata.tables


def test_request_orm_metadata_column_aliased() -> None:
    """The DeclarativeBase-reserved ``metadata`` is aliased to ``metadata_json``."""
    from voicegateway.models.request_model import Request

    cols = {c.name for c in Request.__table__.columns}
    assert "metadata" in cols, f"missing metadata column on requests: {cols}"
    # Python attribute name is metadata_json; SQL column name is "metadata".
    py_attr = "metadata_json"
    assert py_attr in Request.model_fields
