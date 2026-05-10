"""Tests for the ``voicegw onboard`` wizard.

Covers the prompt-and-write happy paths plus the
five-second-timeout-bound provider key validation. Subsequent
v0.1.0 commits add tests for:

  - Ctrl+C cancellation with partial-state cleanup.
  - Smoke-test offering at the end of the wizard.
  - Wizard summary content (AC-VG-ONBOARD-002.3).
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_provider_validation(request, monkeypatch):
    """Replace the live ``health_check()`` plumbing with a stub that
    returns "ok" so prompt-and-write tests do not depend on the
    actual provider plugin being installed (or on network access).

    Tests that exercise the validator directly (``test_validate_*``)
    opt out of this autouse: they need the real function under
    test, with their own monkeypatched registry.
    """
    if request.node.name.startswith("test_validate_"):
        return

    async def _stub(provider: str, api_key: str) -> tuple[str, str | None]:
        return "ok", None

    monkeypatch.setattr("voicegateway.cli.onboard._validate_provider_key", _stub)


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


# ---------------------------------------------------------------------------
# Provider key validation (5-second timeout) — REQ-VG-ONBOARD-002.2
# ---------------------------------------------------------------------------


def _run_wizard_with_validation(monkeypatch, tmp_path, status, message=None):
    """Helper: run the wizard once with the validator stubbed to
    return ``(status, message)``. Returns the CliRunner result.
    """

    async def _stub(provider: str, api_key: str) -> tuple[str, str | None]:
        return status, message

    monkeypatch.setattr("voicegateway.cli.onboard._validate_provider_key", _stub)

    cfg = tmp_path / "voicegw.yaml"
    return runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\nn\n",
    )


def test_onboard_validation_ok_prints_validated(tmp_path, monkeypatch):
    result = _run_wizard_with_validation(monkeypatch, tmp_path, "ok")
    assert result.exit_code == 0
    assert "Provider key validated" in result.output


def test_onboard_validation_timeout_fails_soft(tmp_path, monkeypatch):
    """Timeout is the headline soft-fail per REQ-VG-ONBOARD-002.2.

    The wizard MUST NOT fail; it prints the documented message
    ("validation timed out: your key may still be correct;
    continuing") and the YAML is still written.
    """
    result = _run_wizard_with_validation(monkeypatch, tmp_path, "timeout")
    assert result.exit_code == 0, result.output
    assert "timed out" in result.output
    assert "may still be correct" in result.output
    # And the wizard reached its end-state.
    cfg = tmp_path / "voicegw.yaml"
    assert cfg.exists()


def test_onboard_validation_failed_continues_with_warning(tmp_path, monkeypatch):
    """Auth failure (e.g., 401 unauthorized): wizard continues so
    the typo gets fixed via voicegw doctor or a re-run, not by
    abandoning the wizard mid-flight.
    """
    result = _run_wizard_with_validation(
        monkeypatch, tmp_path, "failed", "authentication declined"
    )
    assert result.exit_code == 0, result.output
    assert "validation failed" in result.output
    assert "voicegw doctor" in result.output


def test_onboard_validation_skipped_for_unknown_provider(tmp_path, monkeypatch):
    """Provider name outside the registry skips live validation
    entirely (the YAML schema validator catches typos at gateway
    construction time).
    """
    result = _run_wizard_with_validation(
        monkeypatch, tmp_path, "skipped", "unknown provider name 'fooprovider'"
    )
    assert result.exit_code == 0, result.output
    assert "Skipping live validation" in result.output


# ---------------------------------------------------------------------------
# _validate_provider_key directly — covers each branch without the
# Typer machinery so the timeout path is exercised end-to-end.
# ---------------------------------------------------------------------------


async def test_validate_returns_ok_when_health_check_true(monkeypatch):
    """Happy path through create_provider + health_check returning True."""
    from voicegateway.cli.onboard import _validate_provider_key

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY",
        {"openai": object},
    )

    class _FakeProvider:
        def __init__(self, *_a, **_kw):
            pass

        async def health_check(self):
            return True

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda name, cfg: _FakeProvider(),
    )

    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "ok"
    assert message is None


async def test_validate_returns_timeout_when_health_check_hangs(monkeypatch):
    """Five-second cap. Use a sentinel future that never completes
    plus a tiny patched timeout so the test does not actually take
    five seconds.
    """
    import asyncio as _aio

    from voicegateway.cli import onboard as onboard_mod

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            await _aio.Event().wait()  # never returns
            return True  # unreachable

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda name, cfg: _FakeProvider(),
    )
    # Lower the cap to 50 ms so the test stays fast.
    monkeypatch.setattr(onboard_mod, "_VALIDATION_TIMEOUT_S", 0.05)

    status, message = await onboard_mod._validate_provider_key("openai", "sk-test")
    assert status == "timeout"
    assert message is None


async def test_validate_returns_failed_when_health_check_returns_false(monkeypatch):
    from voicegateway.cli.onboard import _validate_provider_key

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            return False

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda name, cfg: _FakeProvider(),
    )

    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "failed"
    assert "declined" in (message or "")


async def test_validate_returns_failed_when_health_check_raises(monkeypatch):
    from voicegateway.cli.onboard import _validate_provider_key

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    class _FakeProvider:
        async def health_check(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider",
        lambda name, cfg: _FakeProvider(),
    )

    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "failed"
    assert "connection refused" in (message or "")


async def test_validate_skipped_for_unknown_provider(monkeypatch):
    from voicegateway.cli.onboard import _validate_provider_key

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    status, message = await _validate_provider_key("notathing", "sk-test")
    assert status == "skipped"
    assert "notathing" in (message or "")


# ---------------------------------------------------------------------------
# Ctrl+C cancellation - REQ-VG-ONBOARD-002.4
# ---------------------------------------------------------------------------


def _typer_prompt_kbi_at(target_call: int):
    """Return a typer.prompt replacement that raises KeyboardInterrupt
    on the Nth call (1-indexed). Earlier calls return their default.
    """
    state = {"calls": 0}

    def _fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == target_call:
            raise KeyboardInterrupt()
        return kwargs.get("default", "")

    return _fn


def test_ctrl_c_during_first_prompt_no_partial_config(tmp_path, monkeypatch):
    """Ctrl+C at the very first prompt: no config file should exist."""
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard.typer.prompt",
        _typer_prompt_kbi_at(1),
    )
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)]
    )
    assert result.exit_code == 130
    assert "cancelled" in result.output.lower()
    assert not cfg.exists(), "no partial config should land on Ctrl+C"


def test_ctrl_c_after_config_write_removes_partial_when_new(tmp_path, monkeypatch):
    """Ctrl+C right before daemon install: the partial config the
    wizard wrote (file did not exist before) is removed.
    """
    cfg = tmp_path / "voicegw.yaml"
    # Confirm prompts run fine (1..4) but typer.confirm (call 5) Ctrl+Cs.
    # ...except the wizard does the write BEFORE the daemon prompt
    # only if --install-daemon flag was passed; otherwise the daemon
    # confirm is between prompt 4 and the write. Use --install-daemon
    # so the write happens before the daemon install, then Ctrl+C the
    # _install_daemon call itself by patching DaemonManager to raise.
    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    fake_manager.install.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake_manager),
    )

    result = runner.invoke(
        app,
        ["onboard", "--install-daemon", "--config", str(cfg)],
        # Five prompts answered with defaults; daemon install KBIs.
        input="\n\nsk-test\n\n",
    )
    assert result.exit_code == 130, result.output
    assert "cancelled" in result.output.lower()
    # The partial config that the wizard wrote got rolled back.
    assert not cfg.exists(), (
        f"config file {cfg} should have been removed because it didn't "
        "exist before the wizard ran"
    )


def test_ctrl_c_restores_pre_existing_config_byte_for_byte(tmp_path, monkeypatch):
    """When a config existed before the wizard, Ctrl+C restores it
    exactly. The wizard's partial merge does NOT persist.
    """
    cfg = tmp_path / "voicegw.yaml"
    pre_existing = b"providers:\n  hand_edited:\n    api_key: PRESERVE_ME\n"
    cfg.write_bytes(pre_existing)

    from unittest.mock import MagicMock

    fake_manager = MagicMock()
    fake_manager.install.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(
        "voicegateway.cli.daemon.DaemonManager",
        MagicMock(return_value=fake_manager),
    )

    result = runner.invoke(
        app,
        ["onboard", "--install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\n",
    )
    assert result.exit_code == 130, result.output
    # Original bytes are back on disk - the wizard's partial merge is gone.
    assert cfg.read_bytes() == pre_existing, (
        "pre-existing config was not restored byte-for-byte"
    )


async def test_validate_skipped_when_plugin_not_installed(monkeypatch):
    from voicegateway.cli.onboard import _validate_provider_key

    monkeypatch.setattr(
        "voicegateway.core.registry._PROVIDER_REGISTRY", {"openai": object}
    )

    def _raise_import_error(name, cfg):
        raise ImportError("livekit-plugins-openai not installed")

    monkeypatch.setattr(
        "voicegateway.core.registry.create_provider", _raise_import_error
    )

    status, message = await _validate_provider_key("openai", "sk-test")
    assert status == "skipped"
    assert "plugin not installed" in (message or "")
