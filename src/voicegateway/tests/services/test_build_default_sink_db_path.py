"""Tests for the additive db_path kwarg on _build_default_sink."""

from __future__ import annotations

from typing import Any

from voicegateway.inference.session.attach import _build_default_sink
from voicegateway.services.sinks import LocalSqliteSink, RemoteCollectorSink


def test_db_path_used_on_local_branch(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _RecordingStorage:
        def __init__(self, db_path: str) -> None:
            captured["db_path"] = db_path

    monkeypatch.setattr(
        "voicegateway.services.storage_service.StorageService", _RecordingStorage
    )

    sink = _build_default_sink(None, None, db_path="/tmp/custom-vg.db")

    assert isinstance(sink, LocalSqliteSink)
    assert captured["db_path"] == "/tmp/custom-vg.db"


def test_db_path_ignored_on_fleet_branch() -> None:
    sink = _build_default_sink("https://collector.example.com", "vk_123", db_path="/x")
    assert isinstance(sink, RemoteCollectorSink)


def test_backward_compatible_two_arg_call(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _RecordingStorage:
        def __init__(self, db_path: str) -> None:
            captured["db_path"] = db_path

    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    monkeypatch.setattr(
        "voicegateway.services.storage_service.StorageService", _RecordingStorage
    )

    sink = _build_default_sink(None, None)

    assert isinstance(sink, LocalSqliteSink)
    assert captured["db_path"] == "~/.config/voicegateway/voicegw.db"
