"""Tests for the ``voicegw onboard`` wizard.

This iteration covers the prompt-and-write happy paths. Subsequent
v0.1.0 commits add tests for:

  - Real-time provider key validation (5-second timeout).
  - Ctrl+C cancellation with partial-state cleanup.
  - Smoke-test offering at the end of the wizard.
  - Wizard summary content (AC-VG-ONBOARD-002.3).
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


# Default Typer prompt response stream the happy path consumes:
#   1. project name        - empty (use default "default")
#   2. provider             - empty (use default "openai")
#   3. API key              - "sk-test-fake"
#   4. port                 - empty (use default 8080)
#   5. install daemon       - explicit "n" (no, so we don't touch
#                             real launchctl/systemctl/schtasks
#                             during the test)
_HAPPY_PATH_INPUT = "\n\nsk-test-fake\n\nn\n"


def test_onboard_happy_path_writes_config(tmp_path):
    """Five-question run with all defaults, no daemon install."""
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input=_HAPPY_PATH_INPUT,
    )
    assert result.exit_code == 0, result.output
    assert cfg.exists()

    parsed = yaml.safe_load(cfg.read_text())
    assert parsed["default_project"] == "default"
    assert parsed["projects"]["default"]["name"] == "Default"
    assert parsed["providers"]["openai"]["api_key"] == "sk-test-fake"
    assert parsed["serve"]["port"] == 8080
    assert parsed["cost_tracking"]["enabled"] is True


def test_onboard_custom_project_and_port(tmp_path):
    """Non-default project name and a non-default port flow into yaml."""
    cfg = tmp_path / "voicegw.yaml"
    # tony-pizza, deepgram, sk-test, 9000, n
    inp = "tony-pizza\ndeepgram\nsk-test\n9000\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output

    parsed = yaml.safe_load(cfg.read_text())
    assert parsed["default_project"] == "tony-pizza"
    assert parsed["projects"]["tony-pizza"]["name"] == "Tony Pizza"
    assert parsed["providers"]["deepgram"]["api_key"] == "sk-test"
    assert parsed["serve"]["port"] == 9000


def test_onboard_preserves_existing_yaml(tmp_path):
    """Re-running the wizard merges into existing config rather than
    overwriting it. Idempotency requirement (AC-VG-ONBOARD-002.5).
    """
    cfg = tmp_path / "voicegw.yaml"
    # Pre-existing config that the wizard must NOT clobber.
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"existing": {"api_key": "preserved"}},
                "models": {"stt": {"my-model": {"provider": "existing"}}},
            }
        )
    )

    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input=_HAPPY_PATH_INPUT,
    )
    assert result.exit_code == 0, result.output

    parsed = yaml.safe_load(cfg.read_text())
    # Pre-existing keys survive.
    assert parsed["providers"]["existing"]["api_key"] == "preserved"
    assert parsed["models"]["stt"]["my-model"]["provider"] == "existing"
    # Wizard input also lands.
    assert parsed["providers"]["openai"]["api_key"] == "sk-test-fake"


def test_onboard_unknown_provider_warns_but_continues(tmp_path):
    """A provider name outside the known list does not abort the
    wizard; a yellow warning suggests checking the YAML schema.
    """
    cfg = tmp_path / "voicegw.yaml"
    inp = "default\nfooprovider\nsk-test\n8080\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output
    assert "Unknown provider" in result.output
    parsed = yaml.safe_load(cfg.read_text())
    assert parsed["providers"]["fooprovider"]["api_key"] == "sk-test"


def test_onboard_install_daemon_path_invokes_manager(tmp_path, monkeypatch):
    """When --install-daemon is set, _install_daemon runs and calls
    the manager. Tests inject a fake DaemonManager so we don't drive
    real launchctl/systemctl during the run.
    """
    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    fake_manager_class = MagicMock(return_value=fake_manager)
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        fake_manager_class,
    )

    cfg = tmp_path / "voicegw.yaml"
    # Five prompt responses, no install-daemon prompt because flag is explicit.
    inp = "\n\nsk-test\n\n"
    result = runner.invoke(
        app,
        ["onboard", "--install-daemon", "--config", str(cfg)],
        input=inp,
    )
    assert result.exit_code == 0, result.output
    fake_manager_class.assert_called_once_with()
    fake_manager.install.assert_called_once_with()


def test_onboard_help_renders():
    """``--help`` shows the documented option surface."""
    result = runner.invoke(app, ["onboard", "--help"])
    assert result.exit_code == 0
    assert "--install-daemon" in result.output
    assert "--config" in result.output
