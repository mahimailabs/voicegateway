"""Tests for the framework-agnostic ``voicegw onboard`` wizard.

Four questions: project, storage, port, daemon. No provider or key: VoiceGateway
meters the native instances you ``attach()`` in your agent, so onboard configures
storage + the daemon and prints the integration snippet, then runs ``check``.
"""

from __future__ import annotations

import click
import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _plain(output: str) -> str:
    """Strip ANSI escape codes from CliRunner output."""
    return click.unstyle(output)


# Happy-path prompt stream with --no-install-daemon (daemon prompt skipped):
#   1. project name  - empty (default "default")
#   2. storage path  - empty (default db path)
#   3. port          - empty (default 8080)
#   4. run a check?  - "n" (decline; check-path tests stub the subprocess)
_HAPPY_PATH_INPUT = "\n\n\nn\n"


def test_onboard_writes_provider_less_config(tmp_path):
    """Happy path writes a valid, provider-less config with no plaintext key."""
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
    assert parsed["serve"]["port"] == 8080
    assert parsed["cost_tracking"]["enabled"] is True
    # No provider block, no key: the framework-agnostic contract.
    assert "providers" not in parsed
    assert "api_key" not in cfg.read_text()


def test_onboard_custom_project_storage_port(tmp_path):
    """A non-default project, storage path, and port flow into the yaml."""
    cfg = tmp_path / "voicegw.yaml"
    db = tmp_path / "custom.db"
    inp = f"tony-pizza\n{db}\n9000\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output

    parsed = yaml.safe_load(cfg.read_text())
    assert parsed["default_project"] == "tony-pizza"
    assert parsed["projects"]["tony-pizza"]["name"] == "Tony Pizza"
    assert parsed["serve"]["port"] == 9000
    assert parsed["cost_tracking"]["db_path"] == str(db)
    assert "providers" not in parsed


def test_wizard_yaml_loads_under_schema(tmp_path):
    """The wizard's voicegw.yaml loads through ``GatewayConfig.load``."""
    from voicegateway.core.config import GatewayConfig

    cfg = tmp_path / "voicegw.yaml"
    inp = "tony-pizza\n\n9123\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output

    loaded = GatewayConfig.load(cfg)
    assert loaded.serve.get("port") == 9123
    assert loaded.default_project == "tony-pizza"


def test_onboard_preserves_existing_yaml(tmp_path):
    """Re-running merges into an existing config rather than clobbering it."""
    cfg = tmp_path / "voicegw.yaml"
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
    # Hand-edited keys survive; the wizard adds project + storage + serve.
    assert parsed["providers"]["existing"]["api_key"] == "preserved"
    assert parsed["models"]["stt"]["my-model"]["provider"] == "existing"
    assert parsed["default_project"] == "default"
    assert parsed["cost_tracking"]["enabled"] is True


def test_onboard_install_daemon_path_invokes_manager(tmp_path, monkeypatch):
    """With --install-daemon the daemon manager is installed + started."""
    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager", MagicMock(return_value=fake_manager)
    )
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--install-daemon", "--config", str(cfg)], input="\n\n\nn\n"
    )
    assert result.exit_code == 0, result.output
    # The daemon is installed to serve the exact config that was onboarded.
    fake_manager.install.assert_called_once_with(config_path=str(cfg))


def test_onboard_daemon_install_failure_warns_and_continues(tmp_path, monkeypatch):
    """A daemon-install failure must not abort onboarding: warn and continue.

    The config is already written, so the wizard reaches its summary and exits 0
    with the daemon marked not-installed, instead of crashing with a traceback.
    """
    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    fake_manager.install.side_effect = RuntimeError("launchctl bootstrap failed (5)")
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager", MagicMock(return_value=fake_manager)
    )
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--install-daemon", "--config", str(cfg)], input="\n\n\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert cfg.exists()  # config was still written
    out = _plain(result.output)
    assert "Daemon install failed" in out
    assert "not installed" in out  # summary reflects the failure


def test_onboard_help_renders():
    result = runner.invoke(app, ["onboard", "--help"])
    assert result.exit_code == 0
    plain = _plain(result.output)
    assert "--install-daemon" in plain
    assert "--config" in plain


def test_onboard_wizard_under_one_second(tmp_path):
    """Mocked end-to-end wizard completes well under 1s (no slow import/network)."""
    import time

    cfg = tmp_path / "voicegw.yaml"
    start = time.perf_counter()
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input=_HAPPY_PATH_INPUT,
    )
    elapsed = time.perf_counter() - start
    assert result.exit_code == 0, result.output
    assert elapsed < 1.0, f"Mocked wizard took {elapsed:.2f}s."


# ---------------------------------------------------------------------------
# Summary + integration snippet
# ---------------------------------------------------------------------------


def test_summary_shows_fields_and_attach_snippet(tmp_path):
    """The summary names project/port/config + the attach() integration line."""
    cfg = tmp_path / "voicegw.yaml"
    inp = "tony-pizza\n\n9001\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output
    out = _plain(result.output)
    assert "Onboarding complete" in out
    assert "tony-pizza" in out
    assert "9001" in out
    assert str(cfg) in out
    assert "http://127.0.0.1:9001" in out
    # The integration snippet is the point of onboarding now.
    assert "import voicegateway" in out
    assert 'voicegateway.attach(session, project="tony-pizza")' in out
    assert "VOICEGW_COLLECTOR_URL" in out
    # No provider prompt / provider row.
    assert "Provider" not in out or "provider" not in out.lower().split("add one")[0]
    assert "voicegw doctor" in out


def test_summary_daemon_installed_marker(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr("voicegateway.cli.daemon.DaemonManager", MagicMock())
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--install-daemon", "--config", str(cfg)], input="\n\n\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert "installed and started" in result.output
    assert "not installed" not in result.output


def test_summary_daemon_not_installed_marker(tmp_path):
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input="\n\n\nn\n"
    )
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output
    assert "voicegw onboard --install-daemon" in result.output
    # With no daemon, the summary points at `serve` (nothing is serving yet).
    assert "voicegw serve" in result.output


# ---------------------------------------------------------------------------
# Closing `check` step
# ---------------------------------------------------------------------------


def test_check_runs_when_user_accepts(tmp_path, monkeypatch):
    """Accepting the offer spawns `voicegw check` (not smoke-test)."""
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.utils.cli.onboard.shutil.which", lambda _: "/fake/bin/voicegw"
    )
    fake_run = MagicMock(
        return_value=_sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr("voicegateway.utils.cli.onboard.subprocess.run", fake_run)

    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input="\n\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Check passed" in result.output
    fake_run.assert_called_once()
    args = fake_run.call_args.args[0]
    assert args[0] == "/fake/bin/voicegw"
    assert "check" in args
    assert "smoke-test" not in args
    assert str(cfg) in args


def test_check_skipped_when_user_declines(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    fake_run = MagicMock()
    monkeypatch.setattr("voicegateway.utils.cli.onboard.subprocess.run", fake_run)
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input=_HAPPY_PATH_INPUT,
    )
    assert result.exit_code == 0, result.output
    fake_run.assert_not_called()
    assert "Check passed" not in result.output


def test_check_failure_prints_pointer(tmp_path, monkeypatch):
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.utils.cli.onboard.shutil.which", lambda _: "/fake/bin/voicegw"
    )
    fake_run = MagicMock(
        return_value=_sp.CompletedProcess(
            args=[], returncode=1, stdout="config\nrequest landed FAIL\n", stderr=""
        )
    )
    monkeypatch.setattr("voicegateway.utils.cli.onboard.subprocess.run", fake_run)
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input="\n\n\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "Check failed" in result.output
    assert "voicegw check" in result.output


def test_check_timeout_prints_pointer(tmp_path, monkeypatch):
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.utils.cli.onboard.shutil.which", lambda _: "/fake/bin/voicegw"
    )

    def _raise_timeout(*args, **kwargs):
        raise _sp.TimeoutExpired(cmd=args[0] if args else [], timeout=10.0)

    monkeypatch.setattr(
        "voicegateway.utils.cli.onboard.subprocess.run",
        MagicMock(side_effect=_raise_timeout),
    )
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input="\n\n\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "timed out" in result.output
    assert "voicegw check" in result.output


def test_check_skipped_when_voicegw_not_on_path(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr("voicegateway.utils.cli.onboard.shutil.which", lambda _: None)
    fake_run = MagicMock()
    monkeypatch.setattr("voicegateway.utils.cli.onboard.subprocess.run", fake_run)
    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input="\n\n\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "Could not find 'voicegw' on PATH" in result.output
    fake_run.assert_not_called()


# ---------------------------------------------------------------------------
# Ctrl+C cancellation leaves no partial config
# ---------------------------------------------------------------------------


def _typer_prompt_kbi_at(target_call: int):
    state = {"calls": 0}

    def _fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == target_call:
            raise KeyboardInterrupt()
        return kwargs.get("default", "")

    return _fn


def _typer_confirm_kbi_at(target_call: int):
    state = {"calls": 0}

    def _fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == target_call:
            raise KeyboardInterrupt()
        return kwargs.get("default", False)

    return _fn


@pytest.mark.parametrize(
    ("position", "label"),
    [(1, "project name"), (2, "storage"), (3, "port")],
)
def test_ctrl_c_at_each_prompt_no_partial_config(
    tmp_path, monkeypatch, position, label
):
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard_cli.typer.prompt", _typer_prompt_kbi_at(position)
    )
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)]
    )
    assert result.exit_code == 130, (
        f"KBI at prompt {position} ({label}) did not exit 130"
    )
    assert not cfg.exists(), f"KBI at prompt {position} ({label}) left a partial config"


def test_ctrl_c_at_daemon_confirm_no_partial_config(tmp_path, monkeypatch):
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard_cli.typer.confirm", _typer_confirm_kbi_at(1)
    )
    # No daemon flag so the daemon confirm fires (before the config write).
    result = runner.invoke(app, ["onboard", "--config", str(cfg)], input="\n\n\n")
    assert result.exit_code == 130, result.output
    assert not cfg.exists()


def test_ctrl_c_at_check_confirm_rolls_back_config(tmp_path, monkeypatch):
    """The check confirm fires AFTER the config write; cancelling rolls it back."""
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard_cli.typer.confirm", _typer_confirm_kbi_at(1)
    )
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input="\n\n\n"
    )
    assert result.exit_code == 130, result.output
    assert not cfg.exists()


def test_ctrl_c_restores_pre_existing_config_byte_for_byte(tmp_path, monkeypatch):
    cfg = tmp_path / "voicegw.yaml"
    pre_existing = b"providers:\n  hand_edited:\n    api_key: PRESERVE_ME\n"
    cfg.write_bytes(pre_existing)

    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    fake_manager.install.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager", MagicMock(return_value=fake_manager)
    )
    result = runner.invoke(
        app, ["onboard", "--install-daemon", "--config", str(cfg)], input="\n\n\n"
    )
    assert result.exit_code == 130, result.output
    assert cfg.read_bytes() == pre_existing
