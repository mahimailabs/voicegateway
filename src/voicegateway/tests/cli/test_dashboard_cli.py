"""Tests for ``voicegw dashboard`` (the browser-opener).

After the dashboard fold-in, ``voicegw dashboard`` resolves the
daemon URL from config and (by default) calls ``webbrowser.open(url)``.
This file covers the URL-resolution path and the ``--no-open`` flag.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "test"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "serve": {"host": "0.0.0.0", "port": 8085},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "cli.db"))
    return cfg


def test_dashboard_command_prints_url_and_calls_webbrowser_open(
    cli_config, monkeypatch
):
    """The default behaviour prints the URL and calls webbrowser.open."""
    calls: list[str] = []

    def fake_open(url: str, *_args, **_kwargs) -> bool:
        calls.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "--config", str(cli_config)])

    assert result.exit_code == 0
    # serve.host is 0.0.0.0; the URL is rewritten to localhost so browsers open it.
    assert "http://localhost:8085" in result.output
    assert "0.0.0.0" not in result.output
    assert calls == ["http://localhost:8085"]


def test_dashboard_command_with_no_open_does_not_call_webbrowser(
    cli_config, monkeypatch
):
    """``--no-open`` prints the URL but never calls webbrowser.open."""
    calls: list[str] = []

    def fake_open(url: str, *_args, **_kwargs) -> bool:
        calls.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "--config", str(cli_config), "--no-open"])

    assert result.exit_code == 0
    assert "http://localhost:8085" in result.output
    assert calls == []


def test_dashboard_command_warns_when_browser_open_fails(cli_config, monkeypatch):
    """When webbrowser.open returns False the command prints a warning."""

    def fake_open(_url: str, *_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr("webbrowser.open", fake_open)

    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "--config", str(cli_config)])

    assert result.exit_code == 0
    assert "Could not auto-launch a browser" in result.output
