"""Verify every legacy table is registered as a SQLModel."""

from __future__ import annotations

from dataclasses import is_dataclass

from sqlmodel import SQLModel

import voicegateway.models  # noqa: F401 — registers every model

_EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "config_audit_log",
        "dead_air_events",
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
        "calls",
        "call_legs",
        "workers",
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


def test_request_orm_metadata_column_aliased() -> None:
    """The DeclarativeBase-reserved ``metadata`` is aliased to ``metadata_json``."""
    from voicegateway.models.request_model import Request

    cols = {c.name for c in Request.__table__.columns}
    assert "metadata" in cols, f"missing metadata column on requests: {cols}"
    # Python attribute name is metadata_json; SQL column name is "metadata".
    py_attr = "metadata_json"
    assert py_attr in Request.model_fields
