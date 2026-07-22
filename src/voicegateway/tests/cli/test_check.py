"""Tests for ``voicegw check``: framework-agnostic pipeline validation.

The headline case is the one the legacy ``smoke-test`` got wrong: ``check`` must
PASS on an empty, provider-less config. It drives one synthetic instrumented
request and asserts a request row + a session row land in storage, all inside a
single event loop so the session ContextVar is visible to the write and read.
"""

from __future__ import annotations

import click
import yaml
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def _plain(output: str) -> str:
    return click.unstyle(output)


def _write_config(cfg_dir, *, cost_tracking: bool = True, name: str = "voicegw"):
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / f"{name}.yaml"
    db = cfg_dir / f"{name}.db"
    data = {
        "default_project": "default",
        "projects": {"default": {"name": "Default"}},
        "cost_tracking": {"enabled": cost_tracking, "db_path": str(db)},
    }
    cfg.write_text(yaml.dump(data))
    return cfg


def test_check_passes_on_empty_provider_less_config(tmp_path):
    """The fix: a config with NO providers/models still passes.

    A synthetic request flows through metering + storage and correlates to a
    session, so both ``request landed`` and ``session correlation`` are PASS.
    """
    cfg = _write_config(tmp_path)
    result = runner.invoke(app, ["check", "--config", str(cfg)])
    out = _plain(result.output)
    assert result.exit_code == 0, out
    assert "request landed" in out
    assert "session correlation" in out
    assert "FAIL" not in out
    assert "All checks passed" in out


def test_check_fails_when_storage_disabled(tmp_path):
    cfg = _write_config(tmp_path, cost_tracking=False)
    result = runner.invoke(app, ["check", "--config", str(cfg)])
    out = _plain(result.output)
    assert result.exit_code == 1, out
    assert "config" in out
    assert "storage" in out.lower()


def test_check_unknown_project_fails(tmp_path):
    cfg = _write_config(tmp_path)
    result = runner.invoke(app, ["check", "--config", str(cfg), "--project", "nope"])
    out = _plain(result.output)
    assert result.exit_code == 1, out
    assert "active project" in out


def test_smoke_test_alias_still_works(tmp_path):
    """The hidden ``smoke-test`` alias runs the same check and passes."""
    cfg = _write_config(tmp_path / "alias")
    result = runner.invoke(app, ["smoke-test", "--config", str(cfg)])
    out = _plain(result.output)
    assert result.exit_code == 0, out
    assert "session correlation" in out


def test_check_help_renders():
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    plain = _plain(result.output)
    assert "--config" in plain
    assert "--project" in plain
