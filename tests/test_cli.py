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


def test_version_flag_prints_runtime_version():
    """`voicegw --version` prints the runtime version and exits 0."""
    from voicegateway import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


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


# --------------------------------------------------------------------
# export-costs
# --------------------------------------------------------------------


async def _seed_export_records(db_path: str) -> tuple[str, str, str]:
    """Log three records spanning a 5-day window. Returns (start, mid, end) ISO dates."""
    import datetime as _dt
    import uuid

    from voicegateway.storage.models import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    today = _dt.date.today()

    def at_midday(d: _dt.date) -> float:
        return _dt.datetime.combine(d, _dt.time(12, 0), tzinfo=_dt.UTC).timestamp()

    # Record at start of window, in project alpha
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=at_midday(today - _dt.timedelta(days=4)),
        modality="llm", model_id="openai/gpt-4o-mini", provider="openai",
        project="alpha", input_units=100, output_units=50, cost_usd=0.10,
        pricing_source="genai-prices@0.0.57", status="ok",
    ))
    # Mid-window, project beta
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=at_midday(today - _dt.timedelta(days=2)),
        modality="stt", model_id="deepgram/nova-3", provider="deepgram",
        project="beta", input_units=12.5, output_units=0, cost_usd=0.05,
        pricing_source="local-stt@2026-05-04", status="ok",
    ))
    # Out of window (10 days ago), project alpha
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()),
        timestamp=at_midday(today - _dt.timedelta(days=10)),
        modality="tts", model_id="cartesia/sonic-3", provider="cartesia",
        project="alpha", input_units=200, output_units=0, cost_usd=99.0,
        pricing_source="local-tts@2026-05-04", status="ok",
    ))
    return (
        (today - _dt.timedelta(days=5)).isoformat(),
        (today - _dt.timedelta(days=2)).isoformat(),
        today.isoformat(),
    )


def test_export_costs_csv_default(temp_config, tmp_path, monkeypatch):
    """CSV is the default; header + per-record rows surface, out-of-window record excluded."""
    import asyncio
    import csv
    import io

    db_path = str(tmp_path / "export-csv.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config, "--start", start, "--end", end],
    )
    assert result.exit_code == 0, result.output
    reader = csv.DictReader(io.StringIO(result.output))
    rows = list(reader)
    assert reader.fieldnames == [
        "timestamp", "project", "modality", "provider", "model_id",
        "input_units", "output_units", "cost_usd", "pricing_source", "status",
    ]
    # Two in-window rows; the 99.0 record is excluded.
    assert len(rows) == 2
    by_model = {r["model_id"]: r for r in rows}
    assert by_model["openai/gpt-4o-mini"]["pricing_source"] == "genai-prices@0.0.57"
    assert by_model["deepgram/nova-3"]["project"] == "beta"
    assert float(by_model["openai/gpt-4o-mini"]["cost_usd"]) == 0.10


def test_export_costs_json(temp_config, tmp_path, monkeypatch):
    """JSON format returns a list of dicts with the same columns."""
    import asyncio
    import json

    db_path = str(tmp_path / "export-json.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config,
         "--start", start, "--end", end, "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert all("pricing_source" in row for row in payload)


def test_export_costs_with_project_filter(temp_config, tmp_path, monkeypatch):
    """`--project alpha` excludes beta rows even when they fall in the window."""
    import asyncio
    import csv
    import io

    db_path = str(tmp_path / "export-project.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config,
         "--start", start, "--end", end, "--project", "alpha"],
    )
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert len(rows) == 1
    assert rows[0]["project"] == "alpha"
    assert rows[0]["model_id"] == "openai/gpt-4o-mini"


def test_export_costs_writes_to_file(temp_config, tmp_path, monkeypatch):
    """`--output FILE` writes payload there and prints a summary line on stdout."""
    import asyncio

    db_path = str(tmp_path / "export-file.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))
    out_file = tmp_path / "out.csv"

    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config,
         "--start", start, "--end", end, "--output", str(out_file)],
    )
    assert result.exit_code == 0, result.output
    assert out_file.exists()
    body = out_file.read_text()
    assert body.startswith("timestamp,project,modality")
    assert "Wrote 2 record(s)" in result.output


def test_export_costs_invalid_date_returns_2(temp_config):
    """Malformed `--start` returns exit 2 with helpful error."""
    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config,
         "--start", "not-a-date", "--end", "2026-05-04"],
    )
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_export_costs_invalid_format_returns_2(temp_config):
    """Unknown `--format` returns exit 2 before touching storage."""
    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config,
         "--start", "2026-05-01", "--end", "2026-05-04",
         "--format", "xml"],
    )
    assert result.exit_code == 2
    assert "Unknown format" in result.output


# --------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------


async def _seed_reconcile_records(db_path: str) -> tuple[str, str]:
    """Log VG-side records for the reconcile tests. Returns (start, end) ISO."""
    import datetime as _dt
    import uuid

    from voicegateway.storage.models import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    today = _dt.date.today()
    midday = _dt.datetime.combine(today, _dt.time(12, 0), tzinfo=_dt.UTC).timestamp()

    # Deepgram: 50 minutes total (= 3000 seconds in canonical-file units)
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=midday, modality="stt",
        model_id="deepgram/nova-3", provider="deepgram",
        input_units=30.0, cost_usd=0.090,
    ))
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=midday, modality="stt",
        model_id="deepgram/nova-3", provider="deepgram",
        input_units=20.0, cost_usd=0.060,
    ))
    # OpenAI: 1500 input + 750 output = 2250 tokens total
    await storage.log_request(RequestRecord(
        id=str(uuid.uuid4()), timestamp=midday, modality="llm",
        model_id="openai/gpt-4o-mini", provider="openai",
        input_units=1500, output_units=750, cost_usd=0.001,
    ))
    return (
        (today - _dt.timedelta(days=1)).isoformat(),
        today.isoformat(),
    )


def test_reconcile_text_default(temp_config, tmp_path, monkeypatch):
    """Default text format renders aligned table with provider-specific unit label."""
    import asyncio

    db_path = str(tmp_path / "reconcile-text.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    provider_file = tmp_path / "deepgram.csv"
    provider_file.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,3000.0,2,0.150\n"
    )
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "deepgram",
         "--start", start, "--end", end,
         "--provider-usage-file", str(provider_file)],
    )
    assert result.exit_code == 0, result.output
    assert "nova-3" in result.output
    assert "audio_s" in result.output  # deepgram unit label
    assert "Model" in result.output


def test_reconcile_csv_format(temp_config, tmp_path, monkeypatch):
    """CSV format emits the diff schema header and per-model rows."""
    import asyncio
    import csv
    import io

    db_path = str(tmp_path / "reconcile-csv.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    provider_file = tmp_path / "openai.csv"
    provider_file.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1500,750,1,0.001\n"
    )
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "openai",
         "--start", start, "--end", end,
         "--provider-usage-file", str(provider_file),
         "--format", "csv"],
    )
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "gpt-4o-mini"
    assert float(row["units_diff_abs"]) == 0.0
    assert row["matched_in_vg"] == "True"
    assert row["matched_in_provider"] == "True"


def test_reconcile_json_format(temp_config, tmp_path, monkeypatch):
    """JSON format returns a list of dicts with the diff schema."""
    import asyncio
    import json as _json

    db_path = str(tmp_path / "reconcile-json.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    provider_file = tmp_path / "deepgram.csv"
    provider_file.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,3600.0,2,0.180\n"  # 600s more than VG, 0.030 USD diff
    )
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "deepgram",
         "--start", start, "--end", end,
         "--provider-usage-file", str(provider_file),
         "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    line = payload[0]
    assert line["model"] == "nova-3"
    # Provider has 3600s, VG has 3000s; provider - vg = 600s
    assert line["units_diff_abs"] == pytest.approx(600.0, abs=0.1)
    # Provider had $0.18, VG had $0.15; provider - vg = $0.03
    assert line["cost_diff_abs"] == pytest.approx(0.030, abs=0.001)


def test_reconcile_unknown_provider_returns_2(temp_config, tmp_path):
    """Unknown provider exits 2 before touching storage or files."""
    fake_file = tmp_path / "fake.csv"
    fake_file.write_text("model\n")
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "anthropic",  # not yet supported
         "--start", "2026-05-01", "--end", "2026-05-04",
         "--provider-usage-file", str(fake_file)],
    )
    assert result.exit_code == 2
    assert "Unsupported provider" in result.output


def test_reconcile_missing_provider_file_returns_2(temp_config, tmp_path, monkeypatch):
    """Missing provider-usage-file path exits 2 with a clear error."""
    db_path = str(tmp_path / "reconcile-missing.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "openai",
         "--start", "2026-05-01", "--end", "2026-05-04",
         "--provider-usage-file", str(tmp_path / "absent.csv")],
    )
    assert result.exit_code == 2
    assert "not found" in result.output


def test_reconcile_invalid_format_returns_2(temp_config, tmp_path):
    """Unknown --format exits 2 before touching storage."""
    fake_file = tmp_path / "fake.csv"
    fake_file.write_text("model\n")
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "openai",
         "--start", "2026-05-01", "--end", "2026-05-04",
         "--provider-usage-file", str(fake_file),
         "--format", "xml"],
    )
    assert result.exit_code == 2
    assert "Unknown format" in result.output


def test_reconcile_invalid_date_returns_2(temp_config, tmp_path):
    """Malformed --start exits 2."""
    fake_file = tmp_path / "fake.csv"
    fake_file.write_text("model\n")
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "openai",
         "--start", "not-a-date", "--end", "2026-05-04",
         "--provider-usage-file", str(fake_file)],
    )
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_reconcile_surfaces_missing_models(temp_config, tmp_path, monkeypatch):
    """Models present only in VG (or only in provider file) are still reported."""
    import asyncio
    import json as _json

    db_path = str(tmp_path / "reconcile-asymm.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    # Provider file mentions a model VG never logged (`nova-2`); VG
    # logged `nova-3` which the provider file does not mention.
    provider_file = tmp_path / "deepgram-asymm.csv"
    provider_file.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-2,1000.0,1,0.030\n"
    )
    result = runner.invoke(
        app,
        ["reconcile", "--config", temp_config,
         "--provider", "deepgram",
         "--start", start, "--end", end,
         "--provider-usage-file", str(provider_file),
         "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    by_model = {row["model"]: row for row in payload}
    assert by_model["nova-2"]["matched_in_vg"] is False
    assert by_model["nova-2"]["matched_in_provider"] is True
    assert by_model["nova-3"]["matched_in_vg"] is True
    assert by_model["nova-3"]["matched_in_provider"] is False
