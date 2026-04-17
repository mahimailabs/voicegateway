"""Tests for voicegateway/cli.py — all CLI subcommands."""

import os

import pytest
from typer.testing import CliRunner

from voicegateway.cli import app

runner = CliRunner()


def test_init_creates_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / "voicegw.yaml").exists()


def test_init_custom_output(tmp_path):
    out = str(tmp_path / "custom.yaml")
    result = runner.invoke(app, ["init", "--output", out])
    assert result.exit_code == 0
    assert os.path.exists(out)


def test_status(temp_config):
    result = runner.invoke(app, ["status", "--config", temp_config])
    assert result.exit_code == 0
    assert "Provider Status" in result.output


def test_status_with_project(temp_config):
    result = runner.invoke(app, ["status", "--config", temp_config, "--project", "test-project"])
    assert result.exit_code == 0


def test_costs(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "cli-test.db"))
    result = runner.invoke(app, ["costs", "--config", temp_config])
    assert result.exit_code == 0


def test_projects_list(temp_config):
    result = runner.invoke(app, ["projects", "--config", temp_config])
    assert result.exit_code == 0
    assert "Test Project" in result.output


def test_project_detail(temp_config):
    result = runner.invoke(app, ["project", "test-project", "--config", temp_config])
    assert result.exit_code == 0
    assert "Test Project" in result.output


def test_project_not_found(temp_config):
    result = runner.invoke(app, ["project", "nonexistent", "--config", temp_config])
    assert result.exit_code == 1


def test_logs(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "cli-log.db"))
    result = runner.invoke(app, ["logs", "--config", temp_config])
    assert result.exit_code == 0


def test_serve_help():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "VoiceGateway HTTP API" in result.output


def test_dashboard_help():
    result = runner.invoke(app, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "web dashboard" in result.output
