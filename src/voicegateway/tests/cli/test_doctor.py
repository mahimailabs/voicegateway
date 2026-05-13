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
        "voicegateway.utils.cli.doctor.shutil.which",
        lambda name: f"/usr/local/bin/{name}" if name == "pipx" else shutil.which(name),
    )

    # psutil port-conflict: pretend nothing is on the port.
    monkeypatch.setattr("psutil.net_connections", lambda kind="inet": [])

    # Provider key validation: ok.
    async def _stub_validate(provider, key):
        return "ok", None

    monkeypatch.setattr(
        "voicegateway.utils.cli.onboard._validate_provider_key", _stub_validate
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


# ---------------------------------------------------------------------------
# AC-VG-ONBOARD-006.2: every failed check has a specific fix action.
# ---------------------------------------------------------------------------


_FIX_ACTION_VERBS = (
    "Install",
    "Run",
    "Stop",
    "Re-check",
    "Re-run",
    "Edit",
    "Open",
    "Check",
    "Remove",
    "Set",
)


def _check_modules_with_fail_paths():
    """Return every (check_function, mock_context_factory) pair that
    deterministically triggers the check's fail path.

    Each entry yields a ``(name, check_fn, ctx_factory)`` tuple so a
    parametrized test can pin AC-006.2 contract per check.
    """
    from voicegateway.utils.cli import doctor as d

    def _bare_ctx(**kwargs):
        return d._Context(config_path=None, **kwargs)

    yield (
        "python_version_fail",
        d._check_python_version,
        _bare_ctx,
        lambda monkeypatch: _force_python_version(monkeypatch, (3, 9, 0)),
    )

    yield (
        "pipx_missing",
        d._check_pipx,
        _bare_ctx,
        lambda monkeypatch: monkeypatch.setattr(
            "voicegateway.utils.cli.doctor.shutil.which", lambda _: None
        ),
    )

    yield (
        "daemon_unregistered",
        d._check_daemon_registered,
        lambda: _bare_ctx(daemon_status={"registered": False}),
        None,
    )

    yield (
        "daemon_not_running",
        d._check_daemon_running,
        lambda: _bare_ctx(
            daemon_status={"registered": True, "running": False, "pid": None}
        ),
        None,
    )

    yield (
        "port_in_use",
        d._check_port_conflict,
        _build_port_conflict_ctx,
        _patch_psutil_port_in_use,
    )

    yield (
        "no_provider_configured",
        d._check_provider_configured,
        _build_no_providers_ctx,
        None,
    )

    yield (
        "provider_key_missing",
        d._check_provider_key_valid,
        _build_empty_key_ctx,
        None,
    )


def _force_python_version(monkeypatch, version):
    import sys

    fake = type("FakeVersionInfo", (tuple,), {})(version)
    fake.major, fake.minor, fake.micro = version
    monkeypatch.setattr(sys, "version_info", fake)


def _build_port_conflict_ctx():
    from voicegateway.utils.cli import doctor as d

    fake_gw = MagicMock()
    fake_gw.config.serve = {"port": 8080}
    return d._Context(
        config_path=None,
        gateway=fake_gw,
        daemon_status={"pid": 99999},
    )


def _patch_psutil_port_in_use(monkeypatch):
    import psutil

    fake_conn = MagicMock()
    fake_conn.status = psutil.CONN_LISTEN
    fake_conn.laddr = MagicMock(port=8080)
    fake_conn.pid = 12345  # NOT the daemon's pid (99999)
    monkeypatch.setattr("psutil.net_connections", lambda kind="inet": [fake_conn])


def _build_no_providers_ctx():
    from voicegateway.utils.cli import doctor as d

    fake_gw = MagicMock()
    fake_gw.config.providers = {}
    return d._Context(config_path=None, gateway=fake_gw)


def _build_empty_key_ctx():
    from voicegateway.utils.cli import doctor as d

    fake_gw = MagicMock()
    fake_gw.config.providers = {"openai": {"api_key": ""}}
    return d._Context(config_path=None, gateway=fake_gw)


@pytest.mark.parametrize(
    ("name", "check_fn", "ctx_factory", "patch_fn"),
    list(_check_modules_with_fail_paths()),
)
def test_each_check_fail_path_carries_specific_fix_action(
    name, check_fn, ctx_factory, patch_fn, monkeypatch
):
    """AC-VG-ONBOARD-006.2: every fail row contains a specific
    actionable instruction.

    The contract: the detail string starts with (or contains) at
    least one verb from _FIX_ACTION_VERBS, and either references a
    `voicegw <command>` or names a concrete file/value to change.
    No bare 'see docs' pointers.
    """
    if patch_fn is not None:
        patch_fn(monkeypatch)
    ctx = ctx_factory() if callable(ctx_factory) else ctx_factory

    result = check_fn(ctx)
    assert result.status == "fail", (
        f"{name}: expected fail status, got {result.status!r} "
        f"(detail: {result.detail!r})"
    )
    assert result.detail.strip(), f"{name}: empty detail string"

    has_verb = any(verb in result.detail for verb in _FIX_ACTION_VERBS)
    has_command_ref = "voicegw " in result.detail
    assert has_verb or has_command_ref, (
        f"{name}: detail string lacks actionable instruction. Detail: {result.detail!r}"
    )

    # Anti-pattern: bare 'see docs' is forbidden by AC-006.2.
    assert "see docs" not in result.detail.lower(), (
        f"{name}: detail uses bare 'see docs' pointer. Detail: {result.detail!r}"
    )


# ---------------------------------------------------------------------------
# Per-check ok-path tests: each check returns 'ok' under documented
# happy conditions.
# ---------------------------------------------------------------------------


def test_python_version_check_passes_on_modern_python():
    from voicegateway.utils.cli.doctor import _check_python_version, _Context

    result = _check_python_version(_Context(config_path=None))
    assert result.status == "ok"
    assert "3." in result.detail  # version-string format


def test_pipx_check_passes_when_on_path(monkeypatch):
    from voicegateway.utils.cli.doctor import _check_pipx, _Context

    monkeypatch.setattr(
        "voicegateway.utils.cli.doctor.shutil.which", lambda _: "/usr/local/bin/pipx"
    )
    result = _check_pipx(_Context(config_path=None))
    assert result.status == "ok"


def test_daemon_registered_check_passes_when_status_says_yes():
    from voicegateway.utils.cli.doctor import _check_daemon_registered, _Context

    ctx = _Context(
        config_path=None,
        daemon_status={"registered": True, "plist_path": "/tmp/test.plist"},
    )
    result = _check_daemon_registered(ctx)
    assert result.status == "ok"


def test_daemon_running_check_skips_when_not_registered():
    """The skip path is documented: don't repeat the noise of the
    previous check.
    """
    from voicegateway.utils.cli.doctor import _check_daemon_running, _Context

    ctx = _Context(
        config_path=None,
        daemon_status={"registered": False, "running": False},
    )
    result = _check_daemon_running(ctx)
    assert result.status == "skip"


def test_port_conflict_check_passes_when_port_held_by_our_daemon(monkeypatch):
    """If the listener on the configured port IS the voicegw daemon,
    that's expected and the check passes.
    """
    import psutil

    fake_gw = MagicMock()
    fake_gw.config.serve = {"port": 8080}

    from voicegateway.utils.cli.doctor import _check_port_conflict, _Context

    ctx = _Context(
        config_path=None,
        gateway=fake_gw,
        daemon_status={"pid": 99999},  # our daemon's pid
    )

    fake_conn = MagicMock()
    fake_conn.status = psutil.CONN_LISTEN
    fake_conn.laddr = MagicMock(port=8080)
    fake_conn.pid = 99999  # SAME as daemon's pid
    monkeypatch.setattr("psutil.net_connections", lambda kind="inet": [fake_conn])

    result = _check_port_conflict(ctx)
    assert result.status == "ok"
    assert "voicegw daemon" in result.detail


def test_recent_error_check_passes_when_no_recent_failures(monkeypatch):
    """Storage-side check returns ok when the recent rows are clean."""

    fake_gw = MagicMock()

    async def _no_errors(*args, **kwargs):
        return [{"status": "ok"}, {"status": "ok"}]

    fake_gw.storage.get_recent_requests = _no_errors

    from voicegateway.utils.cli.doctor import _check_recent_error_count, _Context

    ctx = _Context(config_path=None, gateway=fake_gw)
    # asyncio.run is fine inside the check, but we have to hand pytest
    # a non-loop context. Fixture runs sync, so call directly.
    result = _check_recent_error_count(ctx)
    assert result.status == "ok"


def test_mcp_responsive_skips_with_documented_rationale():
    """MCP check is intentionally a skip in v0.1.0; deferred for follow-up."""
    from voicegateway.utils.cli.doctor import _check_mcp_responsive, _Context

    result = _check_mcp_responsive(_Context(config_path=None))
    assert result.status == "skip"
    assert "deferred" in result.detail.lower() or "stdio" in result.detail.lower()


def test_dashboard_reachable_skips_when_no_listener(monkeypatch):
    """A daemon-first install runs only ``voicegw serve``; the dashboard
    is a separate optional process. ``voicegw doctor`` must therefore
    SKIP (not FAIL) the dashboard check on a normal connect-failure --
    otherwise every healthy install reports a red row and exits 1.

    Pins Codex P1.3.
    """
    import httpx

    from voicegateway.utils.cli.doctor import _check_dashboard_reachable, _Context

    def _raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr("httpx.get", _raise_connect_error)

    fake_gw = MagicMock()
    fake_gw.config.dashboard = {"port": 9090}
    ctx = _Context(config_path=None, gateway=fake_gw)
    result = _check_dashboard_reachable(ctx)

    assert result.status == "skip", result.detail
    assert "voicegw dashboard" in result.detail
    assert "127.0.0.1:9090" in result.detail
