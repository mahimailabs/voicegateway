"""Tests for ``voicegw doctor``.

This iteration covers the framework: command registration,
table render, exit-code handling, and a single all-pass smoke
case. The next iteration adds per-check pass + one-fail-each
coverage in detail.
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
    """Minimal voicegw.yaml the doctor can load."""
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "sk-test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": False},
            }
        )
    )
    return cfg


@pytest.fixture
def all_pass(monkeypatch):
    """Stub the slow / OS-side calls so all 10 checks return ok or skip."""
    # Daemon: registered + running + pid populated.
    fake_manager = MagicMock()
    fake_manager.status.return_value = {
        "registered": True,
        "running": True,
        "pid": 12345,
    }
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake_manager),
    )

    # pipx and python checks key off real environment; assume the
    # test runner has both available (it does: we run on Python 3.13).
    # If pipx isn't on the runner's PATH we'd skip those tests on CI;
    # test environments typically have it via the dev install.
    import shutil

    monkeypatch.setattr(
        "voicegateway.cli.doctor.shutil.which",
        lambda name: f"/usr/local/bin/{name}" if name == "pipx" else shutil.which(name),
    )

    # psutil port-conflict: pretend nothing is on the port.
    monkeypatch.setattr("psutil.net_connections", lambda kind="inet": [])

    # Provider key validation: ok.
    async def _stub_validate(provider, key):
        return "ok", None

    monkeypatch.setattr(
        "voicegateway.cli.onboard._validate_provider_key", _stub_validate
    )

    # Dashboard reachable: 200 OK.
    fake_response = MagicMock()
    fake_response.status_code = 200
    monkeypatch.setattr("httpx.get", MagicMock(return_value=fake_response))


# ---------------------------------------------------------------------------
# Framework smoke tests
# ---------------------------------------------------------------------------


def test_doctor_help_renders():
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "diagnostic checks" in result.output.lower()


def test_doctor_renders_ten_numbered_rows(temp_config, all_pass):
    """Every check shows up in a numbered row, 1..10."""
    result = runner.invoke(app, ["doctor", "--config", str(temp_config)])
    out = result.output
    # Numbered rows 1..10. Rich's table aligns the # column right with
    # spaces; check for the digits as standalone tokens.
    for n in range(1, 11):
        assert f" {n} " in out or f"\n{n} " in out, f"row {n} missing from output"


def test_doctor_all_pass_exits_zero(temp_config, all_pass):
    """Every check ok/skip -> exit 0 + 'All checks passed' banner."""
    result = runner.invoke(app, ["doctor", "--config", str(temp_config)])
    assert result.exit_code == 0, result.output
    assert "All checks passed" in result.output


def test_doctor_failure_exits_one(temp_config, monkeypatch):
    """Any failing check -> exit 1 + 'need attention' banner."""
    # Force the python-version check to fail by lowering the runtime
    # version_info tuple. Patch sys.version_info via the doctor's
    # import seam.
    import sys

    fake_version = type("FakeVersionInfo", (tuple,), {})((3, 9, 0))
    fake_version.major = 3
    fake_version.minor = 9
    fake_version.micro = 0
    monkeypatch.setattr(sys, "version_info", fake_version)

    result = runner.invoke(app, ["doctor", "--config", str(temp_config)])
    assert result.exit_code == 1
    assert "need attention" in result.output.lower()
    assert "Python version" in result.output


def test_doctor_renders_skip_status_distinct_from_pass_and_fail(
    temp_config, monkeypatch
):
    """The three statuses (PASS / FAIL / SKIP) all appear when at
    least one check returns each.
    """
    # No DaemonManager so the daemon-running check skips because the
    # registered check fails first. Provider configured -> ok.
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(side_effect=RuntimeError("backend missing")),
    )
    result = runner.invoke(app, ["doctor", "--config", str(temp_config)])
    out = result.output
    assert "PASS" in out
    assert "SKIP" in out  # at least the MCP check + something else
    # FAIL: daemon registered fails because the manager raised.
    assert "FAIL" in out
