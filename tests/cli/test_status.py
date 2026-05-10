"""Tests for the v0.1.0 ``voicegw status`` daemon-first ordering.

The provider-status half is exercised by tests/test_cli.py
(``test_status``); this file targets the daemon-section that was
added on top per design.md decision 4.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


@pytest.fixture
def temp_config(tmp_path):
    """Minimal voicegw.yaml the status command can load."""
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {
                    "openai": {"api_key": "sk-test"},
                },
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": False},
            }
        )
    )
    return cfg


def _patch_manager(monkeypatch, info: dict):
    """Replace DaemonManager so tests can inject a status payload."""
    fake = MagicMock()
    fake.status.return_value = info
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake),
    )


# ---------------------------------------------------------------------------
# Daemon section appears FIRST (decision 4).
# ---------------------------------------------------------------------------


def test_status_renders_daemon_section_before_provider_section(
    temp_config, monkeypatch
):
    _patch_manager(
        monkeypatch,
        {"registered": True, "running": True, "pid": 12345},
    )
    result = runner.invoke(app, ["status", "--config", str(temp_config)])
    assert result.exit_code == 0, result.output

    out = result.output
    daemon_idx = out.find("Daemon")
    provider_idx = out.find("Provider Status")
    assert daemon_idx >= 0, "daemon section missing"
    assert provider_idx >= 0, "provider section missing"
    assert daemon_idx < provider_idx, (
        "decision 4: daemon section must come BEFORE provider section"
    )


def test_status_running_daemon_shows_pid(temp_config, monkeypatch):
    _patch_manager(
        monkeypatch,
        {"registered": True, "running": True, "pid": 12345},
    )
    result = runner.invoke(app, ["status", "--config", str(temp_config)])
    assert result.exit_code == 0, result.output
    assert "12345" in result.output
    # "yes" appears for both Registered and Running rows.
    assert result.output.count("yes") >= 2


def test_status_unregistered_daemon_points_at_onboard(temp_config, monkeypatch):
    _patch_manager(
        monkeypatch,
        {"registered": False, "running": False, "pid": None},
    )
    result = runner.invoke(app, ["status", "--config", str(temp_config)])
    assert result.exit_code == 0, result.output
    assert "voicegw onboard --install-daemon" in result.output


def test_status_handles_daemon_backend_failure(temp_config, monkeypatch):
    """If DaemonManager construction blows up, the provider half still
    renders (the two sections are independent).
    """
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(side_effect=RuntimeError("backend exploded")),
    )
    result = runner.invoke(app, ["status", "--config", str(temp_config)])
    assert result.exit_code == 0, result.output
    assert "Daemon status unavailable" in result.output
    assert "Provider Status" in result.output
    assert "openai" in result.output
