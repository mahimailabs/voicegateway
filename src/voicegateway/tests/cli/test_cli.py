"""Tests for voicegateway/cli.py — all CLI subcommands."""

import asyncio
import os
from pathlib import Path

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
    result = runner.invoke(
        app, ["status", "--config", temp_config, "--project", "test-project"]
    )
    assert result.exit_code == 0


def test_costs(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "cli-test.db"))
    result = runner.invoke(app, ["costs", "--config", temp_config])
    assert result.exit_code == 0


def test_costs_prints_staleness_reminder(temp_config, tmp_path, monkeypatch):
    """Q7: `voicegw costs` ends with a one-line reminder naming the
    pricing sources and pointing at `voicegw reconcile`. The reminder
    is dim-styled but still appears in plain stdout when colour is
    stripped, which is what CliRunner captures.
    """
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "cli-staleness.db"))
    result = runner.invoke(app, ["costs", "--config", temp_config])
    assert result.exit_code == 0
    out = result.output
    assert "Pricing sources" in out
    assert "genai-prices@" in out
    assert "voicegateway-catalog@" in out
    assert "voicegw reconcile" in out


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

    from voicegateway.models.request import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    today = _dt.date.today()

    def at_midday(d: _dt.date) -> float:
        return _dt.datetime.combine(d, _dt.time(12, 0), tzinfo=_dt.UTC).timestamp()

    # Record at start of window, in project alpha
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=at_midday(today - _dt.timedelta(days=4)),
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            project="alpha",
            input_units=100,
            output_units=50,
            cost_usd=0.10,
            pricing_source="genai-prices@0.0.57",
            status="ok",
        )
    )
    # Mid-window, project beta
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=at_midday(today - _dt.timedelta(days=2)),
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            project="beta",
            input_units=12.5,
            output_units=0,
            cost_usd=0.05,
            pricing_source="local-stt@2026-05-04",
            status="ok",
        )
    )
    # Out of window (10 days ago), project alpha
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=at_midday(today - _dt.timedelta(days=10)),
            modality="tts",
            model_id="cartesia/sonic-3",
            provider="cartesia",
            project="alpha",
            input_units=200,
            output_units=0,
            cost_usd=99.0,
            pricing_source="local-tts@2026-05-04",
            status="ok",
        )
    )
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
    # Column names match design §2.1: `model` (not model_id) and
    # `calculated_cost_usd` (not cost_usd).
    assert reader.fieldnames == [
        "timestamp",
        "project",
        "modality",
        "provider",
        "model",
        "input_units",
        "output_units",
        "calculated_cost_usd",
        "pricing_source",
        "status",
    ]
    # Two in-window rows; the 99.0 record is excluded.
    assert len(rows) == 2
    by_model = {r["model"]: r for r in rows}
    assert by_model["openai/gpt-4o-mini"]["pricing_source"] == "genai-prices@0.0.57"
    assert by_model["deepgram/nova-3"]["project"] == "beta"
    assert float(by_model["openai/gpt-4o-mini"]["calculated_cost_usd"]) == 0.10


def test_export_costs_json(temp_config, tmp_path, monkeypatch):
    """JSON format is JSONL (one object per line; no outer array)."""
    import asyncio
    import json

    db_path = str(tmp_path / "export-json.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            start,
            "--end",
            end,
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output

    # JSONL: parse line-by-line. The bare output should NOT be a
    # parsable JSON array.
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert len(lines) == 2, f"expected 2 JSONL records, got {len(lines)}: {lines!r}"
    rows = [json.loads(ln) for ln in lines]
    assert all("pricing_source" in row for row in rows)
    # The bare output is not a valid JSON document (no outer array).
    # Confirms we did not regress to JSON-array format.
    import contextlib

    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(result.output)
        # If it parses at all, it must NOT be a list (JSONL is two
        # concatenated objects which json.loads would reject; we only
        # reach the suppress branch on certain edge cases).
        assert not isinstance(parsed, list), (
            "JSON output regressed to a JSON array; expected JSONL."
        )


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
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            start,
            "--end",
            end,
            "--project",
            "alpha",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert len(rows) == 1
    assert rows[0]["project"] == "alpha"
    assert rows[0]["model"] == "openai/gpt-4o-mini"


def test_export_costs_writes_to_file(temp_config, tmp_path, monkeypatch):
    """`--output FILE` writes payload there and prints a summary line on stdout."""
    import asyncio

    db_path = str(tmp_path / "export-file.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, _, end = asyncio.run(_seed_export_records(db_path))
    out_file = tmp_path / "out.csv"

    result = runner.invoke(
        app,
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            start,
            "--end",
            end,
            "--output",
            str(out_file),
        ],
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
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            "not-a-date",
            "--end",
            "2026-05-04",
        ],
    )
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_export_costs_invalid_format_returns_2(temp_config):
    """Unknown `--format` returns exit 2 before touching storage."""
    result = runner.invoke(
        app,
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-04",
            "--format",
            "xml",
        ],
    )
    assert result.exit_code == 2
    assert "Unknown format" in result.output


def test_export_costs_empty_range_returns_header_only(
    temp_config, tmp_path, monkeypatch
):
    """A window with no records emits only the CSV header row (exit 0)."""
    import asyncio
    import csv
    import io

    db_path = str(tmp_path / "export-empty.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    # Seed records so the DB exists, but request a window 30 days
    # before the earliest seeded record so nothing matches.
    asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert rows == [], f"expected zero data rows for empty window; got {rows!r}"
    # Header still emitted so consumers can detect "valid CSV, just
    # empty" vs "process crashed."
    assert result.output.startswith("timestamp,project,modality")


def test_export_costs_empty_range_jsonl_returns_no_lines(
    temp_config, tmp_path, monkeypatch
):
    """JSONL on an empty window emits zero lines (no array, no errors)."""
    import asyncio

    db_path = str(tmp_path / "export-empty-jsonl.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    asyncio.run(_seed_export_records(db_path))

    result = runner.invoke(
        app,
        [
            "export-costs",
            "--config",
            temp_config,
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert lines == [], f"expected zero JSONL records for empty window; got {lines!r}"


def test_export_costs_missing_start_returns_helpful_error(temp_config):
    """Omitting --start surfaces typer's missing-option error (exit 2)."""
    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config, "--end", "2026-05-04"],
    )
    assert result.exit_code == 2, result.output
    # Typer prints "Missing option" or "required" in stderr; check
    # that one of those clear-language tokens lands in the output.
    out = result.output.lower()
    assert "missing" in out or "required" in out, (
        f"expected a clear missing-option message; got {result.output!r}"
    )


def test_export_costs_missing_end_returns_helpful_error(temp_config):
    """Omitting --end surfaces typer's missing-option error (exit 2)."""
    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config, "--start", "2026-05-01"],
    )
    assert result.exit_code == 2, result.output
    out = result.output.lower()
    assert "missing" in out or "required" in out


def test_export_costs_renders_iso_timestamp_and_fixed_point_cost(
    temp_config, tmp_path, monkeypatch
):
    """Per design §2.1: timestamps are ISO-8601 UTC and costs are not
    rendered in scientific notation. Sub-cent values like 1e-5 must
    surface as fixed-point strings.
    """
    import asyncio
    import csv
    import datetime as _dt
    import io
    import uuid

    from voicegateway.models.request import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    db_path = str(tmp_path / "export-precision.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)

    async def _seed() -> tuple[float, str, str]:
        storage = SQLiteStorage(db_path)
        # Pinned timestamp: 2026-04-15 09:30:00 UTC = 1776418200.0
        ts = _dt.datetime(2026, 4, 15, 9, 30, 0, tzinfo=_dt.UTC).timestamp()
        # 1e-05 would render as "1.5e-05" in default float repr; the
        # export must surface it in fixed-point.
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=ts,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="alpha",
                input_units=1,
                output_units=1,
                cost_usd=0.000015,
                pricing_source="genai-prices@0.0.57",
                status="ok",
            )
        )
        return ts, "2026-04-14", "2026-04-16"

    ts, start, end = asyncio.run(_seed())

    result = runner.invoke(
        app,
        ["export-costs", "--config", temp_config, "--start", start, "--end", end],
    )
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.output)))
    assert len(rows) == 1
    row = rows[0]
    # ISO-8601 UTC: starts with the date, contains "T", ends with
    # +00:00 (or "Z" depending on Python's strftime; isoformat() uses
    # +00:00 for utc-aware datetimes).
    assert row["timestamp"].startswith("2026-04-15T"), (
        f"timestamp must be ISO-8601 starting with the UTC date; "
        f"got {row['timestamp']!r}"
    )
    assert "+00:00" in row["timestamp"]
    # Cost rendered as fixed-point. Specifically NOT in scientific
    # notation. The exact float-to-Decimal conversion via str() can
    # introduce a few extra digits but must remain non-scientific.
    cost = row["calculated_cost_usd"]
    assert "e" not in cost.lower(), (
        f"calculated_cost_usd must not use scientific notation; got {cost!r}"
    )
    assert cost.startswith("0.0000"), f"expected sub-cent fixed-point; got {cost!r}"


# --------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------


async def _seed_reconcile_records(db_path: str) -> tuple[str, str]:
    """Log VG-side records for the reconcile tests. Returns (start, end) ISO."""
    import datetime as _dt
    import uuid

    from voicegateway.models.request import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    today = _dt.date.today()
    midday = _dt.datetime.combine(today, _dt.time(12, 0), tzinfo=_dt.UTC).timestamp()

    # Deepgram: 50 minutes total (= 3000 seconds in canonical-file units)
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=midday,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            input_units=30.0,
            cost_usd=0.090,
        )
    )
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=midday,
            modality="stt",
            model_id="deepgram/nova-3",
            provider="deepgram",
            input_units=20.0,
            cost_usd=0.060,
        )
    )
    # OpenAI: 1500 input + 750 output = 2250 tokens total
    await storage.log_request(
        RequestRecord(
            id=str(uuid.uuid4()),
            timestamp=midday,
            modality="llm",
            model_id="openai/gpt-4o-mini",
            provider="openai",
            input_units=1500,
            output_units=750,
            cost_usd=0.001,
        )
    )
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
        "model,audio_seconds,n_requests,cost_usd\nnova-3,3000.0,2,0.150\n"
    )
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "deepgram",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
        ],
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
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
            "--format",
            "csv",
        ],
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
    """JSON output uses the design §2.2 schema (provider, period, rows, total, flagged_count)."""
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
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "deepgram",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert isinstance(payload, dict)
    assert payload["provider"] == "deepgram"
    assert payload["period"] == {"start": start, "end": end}
    assert "flagged_count" in payload
    assert "total" in payload
    rows = payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    line = rows[0]
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
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "anthropic",  # not yet supported
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-04",
            "--provider-usage-file",
            str(fake_file),
        ],
    )
    assert result.exit_code == 2
    assert "Unsupported provider" in result.output


def test_reconcile_missing_provider_file_returns_2(temp_config, tmp_path, monkeypatch):
    """Missing provider-usage-file path exits 2 with a clear error."""
    db_path = str(tmp_path / "reconcile-missing.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-04",
            "--provider-usage-file",
            str(tmp_path / "absent.csv"),
        ],
    )
    assert result.exit_code == 2
    assert "not found" in result.output


def test_reconcile_invalid_format_returns_2(temp_config, tmp_path):
    """Unknown --format exits 2 before touching storage."""
    fake_file = tmp_path / "fake.csv"
    fake_file.write_text("model\n")
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-04",
            "--provider-usage-file",
            str(fake_file),
            "--format",
            "xml",
        ],
    )
    assert result.exit_code == 2
    assert "Unknown format" in result.output


def test_reconcile_invalid_date_returns_2(temp_config, tmp_path):
    """Malformed --start exits 2."""
    fake_file = tmp_path / "fake.csv"
    fake_file.write_text("model\n")
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            "not-a-date",
            "--end",
            "2026-05-04",
            "--provider-usage-file",
            str(fake_file),
        ],
    )
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_reconcile_threshold_flag_propagates(temp_config, tmp_path, monkeypatch):
    """--threshold lowers the flag bar; rows under default 5% can flip to flagged.

    Drives the CLI with both the default threshold (no flag at 3% drift)
    and an explicit --threshold 1.0 (flag at the same 3% drift). Pins
    that the CLI flag flows through to reconcile(threshold_pct=...).
    """
    import asyncio
    import datetime as _dt
    import json as _json
    import uuid

    from voicegateway.models.request import RequestRecord
    from voicegateway.storage.sqlite import SQLiteStorage

    db_path = str(tmp_path / "reconcile-threshold.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)

    async def _seed() -> tuple[str, str]:
        storage = SQLiteStorage(db_path)
        today = _dt.date.today()
        # Seed a single VG record at $0.97; provider file says
        # $1.00 -> ~3% drift.
        ts = _dt.datetime.combine(
            today - _dt.timedelta(days=1), _dt.time(12), tzinfo=_dt.UTC
        ).timestamp()
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=ts,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="default",
                input_units=1000,
                output_units=500,
                cost_usd=0.97,
                pricing_source="genai-prices@0.0.57",
                status="ok",
            )
        )
        return (
            (today - _dt.timedelta(days=2)).isoformat(),
            today.isoformat(),
        )

    start, end = asyncio.run(_seed())

    provider_file = tmp_path / "openai-threshold.csv"
    provider_file.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
    )

    # Default threshold (5.0): 3% drift should NOT flag.
    result_default = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
            "--format",
            "json",
        ],
    )
    assert result_default.exit_code == 0, result_default.output
    payload_default = _json.loads(result_default.output)
    assert payload_default["rows"][0]["flagged"] is False
    assert payload_default["flagged_count"] == 0

    # Lower threshold (1.0): same 3% drift now flags.
    result_strict = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
            "--format",
            "json",
            "--threshold",
            "1.0",
        ],
    )
    assert result_strict.exit_code == 0, result_strict.output
    payload_strict = _json.loads(result_strict.output)
    assert payload_strict["rows"][0]["flagged"] is True
    assert payload_strict["flagged_count"] == 1


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
        "model,audio_seconds,n_requests,cost_usd\nnova-2,1000.0,1,0.030\n"
    )
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "deepgram",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(provider_file),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    by_model = {row["model"]: row for row in payload["rows"]}
    assert by_model["nova-2"]["matched_in_vg"] is False
    assert by_model["nova-2"]["matched_in_provider"] is True
    assert by_model["nova-3"]["matched_in_vg"] is True
    assert by_model["nova-3"]["matched_in_provider"] is False


# --------------------------------------------------------------------
# reconcile: end-to-end against committed sample fixtures
# --------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_USAGE_EXPORTS_DIR = _REPO_ROOT / "tests" / "fixtures" / "usage_exports"


def test_reconcile_runs_against_committed_openai_sample(
    temp_config, tmp_path, monkeypatch
):
    """End-to-end reconcile against the committed openai-sample.csv.

    Pairs the canonical-schema reference fixture from 4.2 #5 with
    the CLI to confirm the openai code path works against a real
    file shape. _seed_reconcile_records seeds a single
    openai/gpt-4o-mini VG record alongside Deepgram ones, so
    gpt-4o-mini matches on both sides while gpt-4o + gpt-4-turbo
    are provider-only.
    """
    import asyncio
    import json as _json

    db_path = str(tmp_path / "reconcile-openai-sample.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "openai",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(_USAGE_EXPORTS_DIR / "openai-sample.csv"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["provider"] == "openai"
    rows = {row["model"]: row for row in payload["rows"]}
    # Three OpenAI models in the sample fixture; all three appear.
    assert {"gpt-4o-mini", "gpt-4o", "gpt-4-turbo"}.issubset(rows.keys())
    # gpt-4o-mini is seeded on the VG side and present in the
    # provider sample -> matched on both sides.
    assert rows["gpt-4o-mini"]["matched_in_vg"] is True
    assert rows["gpt-4o-mini"]["matched_in_provider"] is True
    # gpt-4o and gpt-4-turbo are provider-only.
    for m in ("gpt-4o", "gpt-4-turbo"):
        assert rows[m]["matched_in_vg"] is False
        assert rows[m]["matched_in_provider"] is True


def test_reconcile_runs_against_committed_deepgram_sample(
    temp_config, tmp_path, monkeypatch
):
    """End-to-end reconcile against the committed deepgram-sample.csv."""
    import asyncio
    import json as _json

    db_path = str(tmp_path / "reconcile-deepgram-sample.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "deepgram",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(_USAGE_EXPORTS_DIR / "deepgram-sample.csv"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["provider"] == "deepgram"
    rows = {row["model"]: row for row in payload["rows"]}
    assert {"nova-3", "nova-2", "flux-general"}.issubset(rows.keys())
    # Per _seed_reconcile_records, VG has nova-3 records; the
    # provider sample also has nova-3 -> matched_in_vg AND
    # matched_in_provider both True for nova-3.
    assert rows["nova-3"]["matched_in_vg"] is True
    assert rows["nova-3"]["matched_in_provider"] is True
    # nova-2 / flux-general are provider-only.
    assert rows["nova-2"]["matched_in_vg"] is False
    assert rows["flux-general"]["matched_in_vg"] is False


def test_reconcile_runs_against_committed_cartesia_sample(
    temp_config, tmp_path, monkeypatch
):
    """End-to-end reconcile against the committed cartesia-sample.csv."""
    import asyncio
    import json as _json

    db_path = str(tmp_path / "reconcile-cartesia-sample.db")
    monkeypatch.setenv("VOICEGW_DB_PATH", db_path)
    start, end = asyncio.run(_seed_reconcile_records(db_path))

    result = runner.invoke(
        app,
        [
            "reconcile",
            "--config",
            temp_config,
            "--provider",
            "cartesia",
            "--start",
            start,
            "--end",
            end,
            "--provider-usage-file",
            str(_USAGE_EXPORTS_DIR / "cartesia-sample.csv"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["provider"] == "cartesia"
    rows = {row["model"]: row for row in payload["rows"]}
    assert {"sonic-3", "sonic-turbo"}.issubset(rows.keys())
    # No VG cartesia records seeded; both rows show provider-only.
    assert all(
        rows[m]["matched_in_provider"] is True and rows[m]["matched_in_vg"] is False
        for m in ("sonic-3", "sonic-turbo")
    )


# ---------------------------------------------------------------------------
# rotate-secret (Q10)
# ---------------------------------------------------------------------------


def _generate_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_rotate_secret_refuses_without_primary(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "rotate-cli.db"))
    monkeypatch.delenv("VOICEGW_SECRET", raising=False)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", _generate_fernet_key())

    result = runner.invoke(app, ["rotate-secret", "--config", temp_config, "--yes"])
    assert result.exit_code == 1
    assert "VOICEGW_SECRET is not set" in result.output


def test_rotate_secret_refuses_without_fallback(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "rotate-cli.db"))
    monkeypatch.setenv("VOICEGW_SECRET", _generate_fernet_key())
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)

    result = runner.invoke(app, ["rotate-secret", "--config", temp_config, "--yes"])
    assert result.exit_code == 1
    assert "VOICEGW_SECRET_FALLBACK is not set" in result.output


def test_rotate_secret_handles_empty_storage(temp_config, tmp_path, monkeypatch):
    """An empty managed_providers table is a no-op and exits cleanly."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "rotate-cli.db"))
    monkeypatch.setenv("VOICEGW_SECRET", _generate_fernet_key())
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", _generate_fernet_key())

    result = runner.invoke(app, ["rotate-secret", "--config", temp_config, "--yes"])
    assert result.exit_code == 0
    assert "No managed_providers rows to rotate" in result.output


def test_rotate_secret_end_to_end(temp_config, tmp_path, monkeypatch):
    """End-to-end: seed rows under primary A, set primary B + fallback
    A, run rotate-secret, confirm rows decrypt under B alone.
    """
    from voicegateway.core.crypto import (
        decrypt as _decrypt,
    )
    from voicegateway.core.crypto import (
        reset_fernet,
    )
    from voicegateway.core.gateway import Gateway

    db_path = tmp_path / "rotate-cli.db"
    monkeypatch.setenv("VOICEGW_DB_PATH", str(db_path))
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)

    primary_a = _generate_fernet_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_a)
    reset_fernet()

    gw = Gateway(config_path=temp_config)
    asyncio.run(
        gw.storage.upsert_managed_provider(
            provider_id="rotate-cli:openai",
            provider_type="openai",
            api_key="sk-rotate-cli",
            project="rotate-cli",
        )
    )

    primary_b = _generate_fernet_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_b)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", primary_a)
    reset_fernet()

    result = runner.invoke(app, ["rotate-secret", "--config", temp_config, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Rotated 1 row" in result.output

    # Drop the fallback. The row must still decrypt under primary B.
    monkeypatch.delenv("VOICEGW_SECRET_FALLBACK", raising=False)
    reset_fernet()
    rows = asyncio.run(gw.storage.list_managed_providers())
    saved = next(r for r in rows if r["provider_id"] == "rotate-cli:openai")
    assert _decrypt(saved["api_key_encrypted"]) == "sk-rotate-cli"


# ---------------------------------------------------------------------------
# smoke-test (AC-001.1 automation harness)
# ---------------------------------------------------------------------------


def test_smoke_test_passes_with_keys_configured(tmp_path, monkeypatch):
    """Default mode constructs each modality through the inference
    factories with stubbed LK plugins, drives a request through the
    wrapper, and confirms the sessions row aggregates correctly.
    """
    import yaml as _yaml

    cfg_path = tmp_path / "smoke.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {
                    "deepgram": {"api_key": "dg-fake"},
                    "openai": {"api_key": "sk-fake"},
                    "cartesia": {"api_key": "ct-fake"},
                },
                "models": {
                    "stt": {
                        "deepgram/nova-3": {
                            "provider": "deepgram",
                            "model": "nova-3",
                        }
                    },
                    "llm": {
                        "openai/gpt-4o-mini": {
                            "provider": "openai",
                            "model": "gpt-4o-mini",
                        }
                    },
                    "tts": {
                        "cartesia/sonic-3": {
                            "provider": "cartesia",
                            "model": "sonic-3",
                        }
                    },
                },
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "projects": {
                    "smoke-proj": {
                        "name": "Smoke",
                        "description": "smoke",
                        "tags": ["test"],
                    }
                },
                "default_project": "smoke-proj",
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "smoke.db"))

    result = runner.invoke(app, ["smoke-test", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "FAIL" not in result.output
    assert "All structural checks passed" in result.output
    assert "voicegateway.inference" in result.output
    # The session-correlation line should name the modalities.
    assert "llm,stt,tts" in result.output or "stt,llm,tts" in result.output


def test_smoke_test_fails_when_provider_keys_missing(tmp_path, monkeypatch):
    """Without configured api_keys the inference preflight raises and
    the smoke test reports per-modality failures with non-zero exit.
    """
    import yaml as _yaml

    cfg_path = tmp_path / "smoke.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {
                    # No api_key — the inference preflight will fail.
                    "deepgram": {},
                    "openai": {},
                    "cartesia": {},
                },
                "models": {
                    "stt": {
                        "deepgram/nova-3": {"provider": "deepgram", "model": "nova-3"}
                    },
                    "llm": {
                        "openai/gpt-4o-mini": {
                            "provider": "openai",
                            "model": "gpt-4o-mini",
                        }
                    },
                    "tts": {
                        "cartesia/sonic-3": {"provider": "cartesia", "model": "sonic-3"}
                    },
                },
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "projects": {
                    "smoke-proj": {
                        "name": "Smoke",
                        "description": "",
                        "tags": [],
                    }
                },
                "default_project": "smoke-proj",
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "smoke.db"))
    # Strip any conftest-set provider env vars so the preflight
    # actually sees no keys.
    for k in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    result = runner.invoke(app, ["smoke-test", "--config", str(cfg_path)])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
    assert "inference.STT" in result.output
    assert "inference.LLM" in result.output
    assert "inference.TTS" in result.output
    # The fail-fast preflight message should surface in the report.
    assert "No API key configured" in result.output


def test_smoke_test_skips_when_storage_disabled(tmp_path, monkeypatch):
    import yaml as _yaml

    cfg_path = tmp_path / "smoke-no-store.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "sk-fake"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": False},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)

    result = runner.invoke(app, ["smoke-test", "--config", str(cfg_path)])
    assert result.exit_code == 1, result.output
    assert "Cost tracking disabled" in result.output


def test_smoke_test_explicit_project_argument(tmp_path, monkeypatch):
    """`--project` selects which project the smoke test runs against,
    overriding default_project. Useful for multi-project deployments
    where the operator wants to validate a specific workload.
    """
    import yaml as _yaml

    cfg_path = tmp_path / "smoke-multi.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "sk-shared"}},
                "models": {
                    "stt": {},
                    "llm": {
                        "openai/gpt-4o-mini": {
                            "provider": "openai",
                            "model": "gpt-4o-mini",
                        }
                    },
                    "tts": {},
                },
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "projects": {
                    "alpha": {"name": "Alpha", "tags": []},
                    "beta": {"name": "Beta", "tags": []},
                },
                "default_project": "alpha",
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "smoke-multi.db"))

    result = runner.invoke(
        app, ["smoke-test", "--config", str(cfg_path), "--project", "beta"]
    )
    assert result.exit_code == 0, result.output
    assert "beta" in result.output


def test_smoke_test_unknown_project_fails_fast(tmp_path, monkeypatch):
    """A typo in --project must fail with a clear "Unknown project"
    message instead of letting the run sail through to a confusing
    "no provider key" deeper in the pipeline.
    """
    import yaml as _yaml

    cfg_path = tmp_path / "smoke-typo.yaml"
    cfg_path.write_text(
        _yaml.dump(
            {
                "providers": {"openai": {"api_key": "sk-shared"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "stacks": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "projects": {"alpha": {"name": "Alpha", "tags": []}},
                "default_project": "alpha",
                "cost_tracking": {"enabled": True},
                "observability": {"latency_tracking": True},
            }
        )
    )
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "smoke-typo.db"))

    result = runner.invoke(
        app, ["smoke-test", "--config", str(cfg_path), "--project", "alfa"]
    )
    assert result.exit_code == 1, result.output
    assert "Unknown project 'alfa'" in result.output
    # The known list should appear so the operator can spot their typo.
    assert "alpha" in result.output


def test_rotate_secret_surfaces_failed_rows(temp_config, tmp_path, monkeypatch):
    """A row encrypted under a key that is not in primary or fallback
    surfaces as a non-zero exit and the provider_id is named in the
    output.
    """
    from cryptography.fernet import Fernet

    from voicegateway.core.crypto import reset_fernet
    from voicegateway.core.gateway import Gateway

    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "rotate-cli.db"))
    secret_file = tmp_path / ".secret"
    monkeypatch.setattr("voicegateway.core.crypto._SECRET_FILE", secret_file)

    # _migrate_plaintext_keys treats anything is_fernet_token() can't
    # decrypt as plaintext and re-encrypts it under the current key.
    # During a real rotation that runs as a one-time operation under
    # both VOICEGW_SECRET and VOICEGW_SECRET_FALLBACK, the orphan
    # token would be incorrectly re-keyed before rotate-secret got
    # to it. Disable the migration here so the orphan path under
    # test is the rotation, not the migration.
    async def _noop_migrate(self, db):
        return None

    monkeypatch.setattr(
        "voicegateway.storage.sqlite.SQLiteStorage._migrate_plaintext_keys",
        _noop_migrate,
    )

    primary_a = _generate_fernet_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_a)
    reset_fernet()

    gw = Gateway(config_path=temp_config)
    asyncio.run(
        gw.storage.upsert_managed_provider(
            provider_id="orphan:deepgram",
            provider_type="deepgram",
            api_key="placeholder",
            project="orphan",
        )
    )

    # Replace the row's ciphertext with a token under a key we will
    # not configure anywhere.
    orphan_token = Fernet(Fernet.generate_key()).encrypt(b"who-knows").decode()
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "rotate-cli.db"))
    try:
        conn.execute(
            "UPDATE managed_providers SET api_key_encrypted = ? "
            "WHERE provider_id = 'orphan:deepgram'",
            (orphan_token,),
        )
        conn.commit()
    finally:
        conn.close()

    primary_b = _generate_fernet_key()
    monkeypatch.setenv("VOICEGW_SECRET", primary_b)
    monkeypatch.setenv("VOICEGW_SECRET_FALLBACK", primary_a)
    reset_fernet()

    result = runner.invoke(app, ["rotate-secret", "--config", temp_config, "--yes"])
    assert result.exit_code == 2, result.output
    assert "orphan:deepgram" in result.output
