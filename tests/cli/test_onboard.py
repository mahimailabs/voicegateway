"""Tests for the ``voicegw onboard`` wizard.

REQ-VG-ONBOARD-002 coverage map (spec asks for):

  - Happy path:
      test_onboard_happy_path_writes_config
      test_onboard_custom_project_and_port
      test_onboard_preserves_existing_yaml
      test_onboard_unknown_provider_warns_but_continues

  - Each cancellation point (REQ-VG-ONBOARD-002.4):
      test_ctrl_c_at_each_prompt_no_partial_config (parametrized
        across prompts 1..4: project / provider / api / port)
      test_ctrl_c_at_daemon_confirm_no_partial_config
      test_ctrl_c_at_smoke_test_confirm_rolls_back_config
      test_ctrl_c_during_first_prompt_no_partial_config (legacy)
      test_ctrl_c_after_config_write_removes_partial_when_new
        (KBI during _install_daemon)
      test_ctrl_c_restores_pre_existing_config_byte_for_byte
        (KBI during _install_daemon, with pre-existing yaml)

  - Key-validation timeout (REQ-VG-ONBOARD-002.2):
      test_onboard_validation_timeout_fails_soft
      test_validate_returns_timeout_when_health_check_hangs

  - Smoke-test success (REQ-VG-ONBOARD-005):
      test_smoke_test_runs_when_user_accepts

  - Smoke-test failure (REQ-VG-ONBOARD-005):
      test_smoke_test_failure_prints_pointer
      test_smoke_test_timeout_prints_pointer
      test_smoke_test_skipped_when_voicegw_not_on_path

Plus the v0.0.5-style help/registration/summary checks and the
direct-call tests for ``_validate_provider_key`` (each branch).
"""

from __future__ import annotations

import click
import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _plain(output: str) -> str:
    """Strip ANSI escape codes from CliRunner output.

    GitHub Actions exports ``FORCE_COLOR=1`` so Typer's Rich help
    renderer splits option flags across bold spans
    (``\\x1b[1m-\\x1b[0m\\x1b[1m-install-daemon\\x1b[0m``), which
    breaks substring matches against ``--install-daemon`` etc.
    ``click.unstyle`` is the canonical way to normalise the output.
    """
    return click.unstyle(output)


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
#   6. smoke test           - explicit "n" (skip; tests that drive
#                             the smoke-test path stub subprocess
#                             explicitly)
_HAPPY_PATH_INPUT = "\n\nsk-test-fake\n\nn\nn\n"


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
    # Trailing "n" declines the wizard's smoke-test offering.
    inp = "tony-pizza\ndeepgram\nsk-test\n9000\nn\nn\n"
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
    # Trailing "n" declines the smoke-test offering.
    inp = "default\nfooprovider\nsk-test\n8080\nn\nn\n"
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
    # Four prompt responses (no daemon prompt since flag is explicit)
    # plus "n" to decline the smoke-test offering.
    inp = "\n\nsk-test\n\nn\n"
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
    plain = _plain(result.output)
    assert "--install-daemon" in plain
    assert "--config" in plain


def test_onboard_wizard_mocked_end_to_end_under_one_second(tmp_path):
    """Mocked end-to-end wizard run completes well under 1s.

    The 60-second budget in design.md §6 acceptance #5 is the
    stranger-test wall clock from curl through first call. With
    DaemonManager mocked + provider validation stubbed (the real
    network call is the only second-scale cost), the cli itself
    must finish in negligible time so the 60-second budget hinges
    only on the network-bound bits.

    A regression that adds a slow import, a sleep, or an
    accidental network call to the wizard surface trips this gate
    immediately, without depending on the stranger-test.
    """
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
    assert elapsed < 1.0, (
        f"Mocked wizard took {elapsed:.2f}s. The 60-second wall-clock "
        "budget for the stranger-test hinges on the cli surface itself "
        "being fast; a regression here means a slow import, sleep, or "
        "accidental network call landed in the wizard path."
    )


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
    # Trailing "n" declines the smoke-test offering.
    return runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\nn\nn\n",
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


def _typer_confirm_kbi_at(target_call: int):
    """Return a typer.confirm replacement that raises KeyboardInterrupt
    on the Nth call (1-indexed). Earlier calls return the default.
    """
    state = {"calls": 0}

    def _fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == target_call:
            raise KeyboardInterrupt()
        return kwargs.get("default", False)

    return _fn


@pytest.mark.parametrize(
    ("position", "label"),
    [
        (1, "project name"),
        (2, "provider"),
        (3, "API key"),
        (4, "port"),
    ],
)
def test_ctrl_c_at_each_prompt_no_partial_config(
    tmp_path, monkeypatch, position, label
):
    """Cancellation at every prompt position 1..4 leaves no partial
    config on disk. Spec calls for 'each cancellation point'.
    """
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard.typer.prompt",
        _typer_prompt_kbi_at(position),
    )
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)]
    )
    assert result.exit_code == 130, (
        f"KBI at prompt {position} ({label}) did not exit 130"
    )
    assert not cfg.exists(), (
        f"KBI at prompt {position} ({label}) left a partial config at {cfg}"
    )


def test_ctrl_c_at_daemon_confirm_no_partial_config(tmp_path, monkeypatch):
    """Cancellation at the daemon-install confirm prompt (typer.confirm,
    not typer.prompt) leaves no partial config: the write happens AFTER
    this confirm.
    """
    cfg = tmp_path / "voicegw.yaml"
    monkeypatch.setattr(
        "voicegateway.cli.onboard.typer.confirm",
        _typer_confirm_kbi_at(1),
    )
    # Run without --install-daemon / --no-install-daemon so the
    # daemon confirm prompt fires (it's the first typer.confirm call).
    result = runner.invoke(
        app,
        ["onboard", "--config", str(cfg)],
        input="\n\nsk-test\n\n",
    )
    assert result.exit_code == 130, result.output
    assert not cfg.exists()


def test_ctrl_c_at_smoke_test_confirm_rolls_back_config(tmp_path, monkeypatch):
    """The smoke-test confirm fires AFTER the config write. Cancelling
    here triggers the rollback path: a brand-new config gets unlinked,
    a pre-existing config gets restored byte-for-byte. Verifies the
    new-file branch.
    """
    cfg = tmp_path / "voicegw.yaml"
    # Run with --no-install-daemon so the only typer.confirm in the
    # wizard body is the smoke-test offer.
    monkeypatch.setattr(
        "voicegateway.cli.onboard.typer.confirm",
        _typer_confirm_kbi_at(1),
    )
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\n",
    )
    assert result.exit_code == 130, result.output
    # Config was written, then the smoke-test KBI fired, then rollback
    # unlinked the new file.
    assert not cfg.exists()


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


# ---------------------------------------------------------------------------
# Wizard final summary - AC-VG-ONBOARD-002.3
# ---------------------------------------------------------------------------


def test_summary_shows_every_configured_field(tmp_path):
    """The summary names project, provider, port, daemon status,
    config path, and the dashboard URL.
    """
    cfg = tmp_path / "voicegw.yaml"
    # Trailing "n" declines the smoke-test offering.
    inp = "tony-pizza\ndeepgram\nsk-test\n9001\nn\nn\n"
    result = runner.invoke(
        app, ["onboard", "--no-install-daemon", "--config", str(cfg)], input=inp
    )
    assert result.exit_code == 0, result.output

    out = result.output
    # Each configured field surfaces verbatim.
    assert "Onboarding complete" in out
    assert "tony-pizza" in out
    assert "deepgram" in out
    assert "9001" in out
    assert str(cfg) in out
    # Dashboard URL is the v0.0.5 default port (9090) on localhost.
    assert "http://127.0.0.1:9090" in out
    # Diagnostic next-steps appear.
    assert "voicegw status" in out
    assert "voicegw doctor" in out


def test_summary_marks_daemon_as_installed_when_flag_passed(tmp_path, monkeypatch):
    """With --install-daemon the summary reports the daemon as
    installed; the explicit phrase exists so a future regression
    that flips the boolean is caught.
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr("voicegateway.cli.daemon.DaemonManager", MagicMock())
    cfg = tmp_path / "voicegw.yaml"
    # Trailing "n" declines the smoke-test offering.
    result = runner.invoke(
        app,
        ["onboard", "--install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "installed and started" in result.output
    assert "not installed" not in result.output


def test_summary_marks_daemon_as_not_installed_with_pointer_when_skipped(tmp_path):
    """When the daemon is declined, the summary tells the user
    exactly how to enable it later.
    """
    cfg = tmp_path / "voicegw.yaml"
    # Trailing "n" declines the smoke-test offering.
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input="\n\nsk-test\n\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output
    assert "voicegw onboard --install-daemon" in result.output


# ---------------------------------------------------------------------------
# Smoke-test offering - REQ-VG-ONBOARD-005
# ---------------------------------------------------------------------------


def test_smoke_test_runs_when_user_accepts(tmp_path, monkeypatch):
    """User accepts (Y by default) -> voicegw smoke-test subprocess
    runs with --config pointing at the wizard's path. Exit 0
    surfaces as a green pass.
    """
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.cli.onboard.shutil.which",
        lambda _: "/fake/bin/voicegw",
    )
    fake_run = MagicMock(
        return_value=_sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr("voicegateway.cli.onboard.subprocess.run", fake_run)

    cfg = tmp_path / "voicegw.yaml"
    # Trailing "y" accepts the smoke-test offer.
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        # 5 prompts (project, provider, api, port, smoke-test);
        # daemon prompt skipped due to --no-install-daemon flag.
        input="\n\nsk-test\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Smoke test passed" in result.output
    fake_run.assert_called_once()
    args = fake_run.call_args.args[0]
    assert args[0] == "/fake/bin/voicegw"
    assert "smoke-test" in args
    assert "--config" in args
    assert str(cfg) in args


def test_smoke_test_skipped_when_user_declines(tmp_path, monkeypatch):
    """User declines (n) -> no subprocess.run for smoke-test."""
    from unittest.mock import MagicMock

    fake_run = MagicMock()
    monkeypatch.setattr("voicegateway.cli.onboard.subprocess.run", fake_run)

    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        input=_HAPPY_PATH_INPUT,
    )
    assert result.exit_code == 0, result.output
    fake_run.assert_not_called()
    assert "Smoke test passed" not in result.output


def test_smoke_test_failure_prints_pointer(tmp_path, monkeypatch):
    """Non-zero smoke-test exit prints a yellow warning with the
    manual-run command so the user can debug.
    """
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.cli.onboard.shutil.which",
        lambda _: "/fake/bin/voicegw",
    )
    fake_run = MagicMock(
        return_value=_sp.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="config\nfailed: details here\n",
        )
    )
    monkeypatch.setattr("voicegateway.cli.onboard.subprocess.run", fake_run)

    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        # 5 prompts (project, provider, api, port, smoke-test);
        # daemon prompt skipped due to --no-install-daemon flag.
        input="\n\nsk-test\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Smoke test failed" in result.output
    # Includes the user-runnable manual command.
    assert "voicegw smoke-test" in result.output


def test_smoke_test_timeout_prints_pointer(tmp_path, monkeypatch):
    """A 10-second timeout fires soft: print a manual-run pointer
    and continue (do NOT fail the wizard).
    """
    import subprocess as _sp
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voicegateway.cli.onboard.shutil.which",
        lambda _: "/fake/bin/voicegw",
    )

    def _raise_timeout(*args, **kwargs):
        raise _sp.TimeoutExpired(cmd=args[0] if args else [], timeout=10.0)

    monkeypatch.setattr(
        "voicegateway.cli.onboard.subprocess.run",
        MagicMock(side_effect=_raise_timeout),
    )

    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        # 5 prompts (project, provider, api, port, smoke-test);
        # daemon prompt skipped due to --no-install-daemon flag.
        input="\n\nsk-test\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "timed out" in result.output
    assert "voicegw smoke-test" in result.output


def test_smoke_test_skipped_when_voicegw_not_on_path(tmp_path, monkeypatch):
    """If shutil.which returns None for voicegw, skip the smoke
    test with a soft warning rather than crashing the wizard.
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr("voicegateway.cli.onboard.shutil.which", lambda _: None)
    fake_run = MagicMock()
    monkeypatch.setattr("voicegateway.cli.onboard.subprocess.run", fake_run)

    cfg = tmp_path / "voicegw.yaml"
    result = runner.invoke(
        app,
        ["onboard", "--no-install-daemon", "--config", str(cfg)],
        # 5 prompts (project, provider, api, port, smoke-test);
        # daemon prompt skipped due to --no-install-daemon flag.
        input="\n\nsk-test\n\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Could not find 'voicegw' on PATH" in result.output
    fake_run.assert_not_called()


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
