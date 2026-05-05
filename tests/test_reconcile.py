"""Smoke tests for `voicegateway/reconcile.py` diff math.

Comprehensive CLI-side tests (CliRunner) belong to Phase 4.3 #5.
These tests exercise the parse + aggregate + diff logic directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway import reconcile


def test_parse_provider_file_openai_csv(tmp_path):
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000000,500000,500,0.225\n"
    )
    parsed = reconcile.parse_provider_file("openai", path)
    assert parsed["gpt-4o-mini"]["units"] == 1_500_000  # input + output
    assert parsed["gpt-4o-mini"]["cost"] == pytest.approx(0.225, abs=0.001)


def test_parse_provider_file_openai_csv_multiple_models(tmp_path):
    """A real OpenAI export lists several models; each lands as its own key."""
    path = tmp_path / "openai-multi.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000000,500000,500,0.225\n"
        "gpt-4o,200000,100000,200,0.900\n"
        "gpt-4-turbo,50000,25000,80,1.200\n"
    )
    parsed = reconcile.parse_provider_file("openai", path)
    assert set(parsed) == {"gpt-4o-mini", "gpt-4o", "gpt-4-turbo"}
    assert parsed["gpt-4o-mini"]["units"] == 1_500_000
    assert parsed["gpt-4o"]["units"] == 300_000
    assert parsed["gpt-4-turbo"]["units"] == 75_000
    # n_requests carries through per row.
    assert parsed["gpt-4o"]["n_requests"] == 200
    assert parsed["gpt-4-turbo"]["cost"] == pytest.approx(1.200, abs=0.001)


def test_parse_provider_file_openai_csv_handles_missing_columns(tmp_path):
    """Rows missing optional columns surface as zero rather than KeyError."""
    path = tmp_path / "openai-sparse.csv"
    # n_requests and cost_usd intentionally absent to confirm the
    # `row.get(..., 0) or 0` fallback works against real-world
    # exports that may omit columns the operator did not export.
    path.write_text(
        "model,input_tokens,output_tokens\n"
        "gpt-4o-mini,1000,500\n"
    )
    parsed = reconcile.parse_provider_file("openai", path)
    assert parsed["gpt-4o-mini"]["units"] == 1500
    assert parsed["gpt-4o-mini"]["cost"] == 0.0
    assert parsed["gpt-4o-mini"]["n_requests"] == 0.0


def test_parse_provider_file_deepgram_csv(tmp_path):
    path = tmp_path / "deepgram.csv"
    path.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,180000.0,1500,8.700\n"
    )
    parsed = reconcile.parse_provider_file("deepgram", path)
    assert parsed["nova-3"]["units"] == pytest.approx(180000.0, abs=0.1)
    assert parsed["nova-3"]["cost"] == pytest.approx(8.700, abs=0.001)


def test_parse_provider_file_deepgram_csv_multiple_models(tmp_path):
    """A real Deepgram export may list nova-3, nova-2, and flux side-by-side."""
    path = tmp_path / "deepgram-multi.csv"
    path.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,180000.0,1500,8.700\n"
        "nova-2,90000.0,800,4.350\n"
        "flux-general,30000.0,250,1.450\n"
    )
    parsed = reconcile.parse_provider_file("deepgram", path)
    assert set(parsed) == {"nova-3", "nova-2", "flux-general"}
    assert parsed["nova-3"]["units"] == pytest.approx(180000.0, abs=0.1)
    assert parsed["nova-2"]["units"] == pytest.approx(90000.0, abs=0.1)
    assert parsed["flux-general"]["units"] == pytest.approx(30000.0, abs=0.1)
    assert parsed["nova-2"]["n_requests"] == 800
    assert parsed["flux-general"]["cost"] == pytest.approx(1.450, abs=0.001)


def test_parse_provider_file_deepgram_csv_handles_missing_columns(tmp_path):
    """Rows missing n_requests + cost_usd surface as zero, not KeyError."""
    path = tmp_path / "deepgram-sparse.csv"
    path.write_text(
        "model,audio_seconds\n"
        "nova-3,12000.0\n"
    )
    parsed = reconcile.parse_provider_file("deepgram", path)
    assert parsed["nova-3"]["units"] == pytest.approx(12000.0, abs=0.1)
    assert parsed["nova-3"]["cost"] == 0.0
    assert parsed["nova-3"]["n_requests"] == 0.0


def test_parse_provider_file_deepgram_csv_units_are_seconds_not_minutes(
    tmp_path,
):
    """Canonical schema's units column is `audio_seconds`; the parser must not
    treat it as minutes and silently scale by 60. VG-side conversion (minutes
    -> seconds) happens in aggregate_vg_records, not here.
    """
    path = tmp_path / "deepgram-units.csv"
    path.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,3600.0,30,0.258\n"
    )
    parsed = reconcile.parse_provider_file("deepgram", path)
    # 3600 seconds = 60 minutes; the parser must surface 3600,
    # not 60 (which would happen if it confused units).
    assert parsed["nova-3"]["units"] == pytest.approx(3600.0, abs=0.1)


def test_parse_provider_file_cartesia_json(tmp_path):
    path = tmp_path / "cartesia.json"
    path.write_text(json.dumps([
        {"model": "sonic-3", "characters": 2_500_000, "credits": 250_000,
         "n_requests": 1000, "cost_usd": 30.0},
    ]))
    parsed = reconcile.parse_provider_file("cartesia", path)
    assert parsed["sonic-3"]["units"] == 2_500_000


def test_parse_provider_file_cartesia_csv(tmp_path):
    """Cartesia parser also accepts the canonical CSV shape (not just JSON)."""
    path = tmp_path / "cartesia.csv"
    path.write_text(
        "model,characters,n_requests,cost_usd\n"
        "sonic-3,2500000,1000,30.0\n"
    )
    parsed = reconcile.parse_provider_file("cartesia", path)
    assert parsed["sonic-3"]["units"] == 2_500_000
    assert parsed["sonic-3"]["cost"] == pytest.approx(30.0, abs=0.01)
    assert parsed["sonic-3"]["n_requests"] == 1000


def test_parse_provider_file_cartesia_csv_multiple_models(tmp_path):
    """Real Cartesia exports may list sonic-3 + older voice models."""
    path = tmp_path / "cartesia-multi.csv"
    path.write_text(
        "model,characters,n_requests,cost_usd\n"
        "sonic-3,2500000,1000,30.0\n"
        "sonic-turbo,800000,400,9.6\n"
    )
    parsed = reconcile.parse_provider_file("cartesia", path)
    assert set(parsed) == {"sonic-3", "sonic-turbo"}
    assert parsed["sonic-3"]["units"] == 2_500_000
    assert parsed["sonic-turbo"]["units"] == 800_000
    assert parsed["sonic-turbo"]["cost"] == pytest.approx(9.6, abs=0.01)


def test_parse_provider_file_cartesia_csv_handles_missing_columns(tmp_path):
    """Sparse CSV (no n_requests, no cost_usd) returns zeros, not KeyError."""
    path = tmp_path / "cartesia-sparse.csv"
    path.write_text(
        "model,characters\n"
        "sonic-3,500000\n"
    )
    parsed = reconcile.parse_provider_file("cartesia", path)
    assert parsed["sonic-3"]["units"] == 500_000
    assert parsed["sonic-3"]["cost"] == 0.0
    assert parsed["sonic-3"]["n_requests"] == 0.0


def test_parse_provider_file_cartesia_units_are_characters(tmp_path):
    """Cartesia bills via credits but our canonical column is characters.

    A future refactor should not silently switch the units column to
    `credits` (which Cartesia also exports). The parser must read
    `characters` to stay aligned with VG's input_units (TTS character
    count) per design §3.2.
    """
    path = tmp_path / "cartesia-units.csv"
    # Note: characters and credits are intentionally different to
    # confirm the parser reads the right column.
    path.write_text(
        "model,characters,credits,n_requests,cost_usd\n"
        "sonic-3,2500000,250000,1000,30.0\n"
    )
    parsed = reconcile.parse_provider_file("cartesia", path)
    assert parsed["sonic-3"]["units"] == 2_500_000, (
        "Cartesia parser must read `characters`, not `credits`."
    )


# Sample fixtures committed under tests/fixtures/usage_exports/
# anchor the parser against committed reference data rather than
# inline strings. This double-checks the canonical schema against
# real files (matters when a future docs change drifts the schema
# and the parser does not).
_FIXTURES_USAGE_DIR = (
    Path(__file__).parent / "fixtures" / "usage_exports"
)


def test_parse_provider_file_loads_committed_openai_sample():
    """The committed openai-sample.csv parses cleanly to known values."""
    parsed = reconcile.parse_provider_file(
        "openai", _FIXTURES_USAGE_DIR / "openai-sample.csv"
    )
    # Three models documented in the fixture README.
    assert set(parsed) == {"gpt-4o-mini", "gpt-4o", "gpt-4-turbo"}
    # Pinned values; updating the fixture requires updating these.
    assert parsed["gpt-4o-mini"]["units"] == 3_750_000  # 2.5M + 1.25M
    assert parsed["gpt-4o-mini"]["cost"] == pytest.approx(0.5625, abs=0.0001)
    assert parsed["gpt-4o"]["units"] == 750_000
    assert parsed["gpt-4-turbo"]["n_requests"] == 250


def test_parse_provider_file_loads_committed_deepgram_sample():
    """The committed deepgram-sample.csv parses cleanly to known values."""
    parsed = reconcile.parse_provider_file(
        "deepgram", _FIXTURES_USAGE_DIR / "deepgram-sample.csv"
    )
    assert set(parsed) == {"nova-3", "nova-2", "flux-general"}
    assert parsed["nova-3"]["units"] == pytest.approx(180_000.0, abs=0.1)
    assert parsed["nova-2"]["units"] == pytest.approx(90_000.0, abs=0.1)
    assert parsed["flux-general"]["cost"] == pytest.approx(1.450, abs=0.001)


def test_parse_provider_file_loads_committed_cartesia_sample():
    """The committed cartesia-sample.csv parses cleanly to known values."""
    parsed = reconcile.parse_provider_file(
        "cartesia", _FIXTURES_USAGE_DIR / "cartesia-sample.csv"
    )
    assert set(parsed) == {"sonic-3", "sonic-turbo"}
    assert parsed["sonic-3"]["units"] == 2_500_000
    assert parsed["sonic-turbo"]["units"] == 800_000
    assert parsed["sonic-turbo"]["cost"] == pytest.approx(9.600, abs=0.001)


def test_parse_provider_file_unknown_provider_raises(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("model\n")
    with pytest.raises(ValueError, match="unsupported provider"):
        reconcile.parse_provider_file("anthropic", path)


def test_parse_provider_file_unknown_extension_raises(tmp_path):
    path = tmp_path / "x.tsv"
    path.write_text("model\n")
    with pytest.raises(ValueError, match="unrecognized"):
        reconcile.parse_provider_file("openai", path)


def test_parse_provider_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        reconcile.parse_provider_file("openai", tmp_path / "not-here.csv")


def test_aggregate_vg_records_deepgram_minutes_to_seconds():
    """STT records carry minutes in input_units; reconcile compares against seconds."""
    records = [
        {"model_id": "deepgram/nova-3", "modality": "stt",
         "input_units": 1.0, "cost_usd": 0.005},
        {"model_id": "deepgram/nova-3", "modality": "stt",
         "input_units": 2.5, "cost_usd": 0.0125},
    ]
    agg = reconcile.aggregate_vg_records("deepgram", records)
    assert agg["nova-3"]["units"] == pytest.approx(3.5 * 60, abs=0.1)
    assert agg["nova-3"]["cost"] == pytest.approx(0.0175, abs=0.001)


def test_aggregate_vg_records_filters_other_providers():
    """Records from other providers are skipped."""
    records = [
        {"model_id": "deepgram/nova-3", "modality": "stt",
         "input_units": 1.0, "cost_usd": 0.005},
        {"model_id": "openai/gpt-4o-mini", "modality": "llm",
         "input_units": 100, "cost_usd": 0.001},
    ]
    agg = reconcile.aggregate_vg_records("deepgram", records)
    assert "gpt-4o-mini" not in agg
    assert agg["nova-3"]["units"] == pytest.approx(60.0, abs=0.1)


def test_aggregate_vg_records_filters_other_modalities():
    """Records with the right provider but wrong modality are skipped.

    Catches the multi-modality bug: an `openai/whisper-1` STT row
    must NOT be summed into the OpenAI LLM reconcile (where it would
    show up as bogus token counts).
    """
    records = [
        {"model_id": "openai/gpt-4o-mini", "modality": "llm",
         "input_units": 1000, "output_units": 500, "cost_usd": 0.001},
        {"model_id": "openai/whisper-1", "modality": "stt",
         "input_units": 5.0, "cost_usd": 0.030},
        {"model_id": "openai/tts-1", "modality": "tts",
         "input_units": 200, "cost_usd": 0.003},
    ]
    agg = reconcile.aggregate_vg_records("openai", records)
    assert set(agg.keys()) == {"gpt-4o-mini"}
    assert agg["gpt-4o-mini"]["units"] == 1500.0
    assert agg["gpt-4o-mini"]["cost"] == pytest.approx(0.001, abs=0.0001)


def test_aggregate_vg_records_openai_sums_input_and_output():
    """OpenAI: VG units = input_tokens + output_tokens, matching the canonical file."""
    records = [
        {"model_id": "openai/gpt-4o-mini", "modality": "llm",
         "input_units": 1000, "output_units": 500, "cost_usd": 0.001},
    ]
    agg = reconcile.aggregate_vg_records("openai", records)
    assert agg["gpt-4o-mini"]["units"] == 1500.0


def test_reconcile_perfect_match(tmp_path):
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,0.001\n"
    )
    records = [
        {"model_id": "openai/gpt-4o-mini", "modality": "llm",
         "input_units": 1000, "output_units": 500, "cost_usd": 0.001},
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    line = lines[0]
    assert line.units_diff_abs == 0
    assert line.cost_diff_abs == pytest.approx(0.0, abs=0.0001)
    assert line.matched_in_vg is True
    assert line.matched_in_provider is True


def test_reconcile_surfaces_divergence(tmp_path):
    """When VG and the provider disagree, the diff carries the gap."""
    path = tmp_path / "deepgram.csv"
    path.write_text(
        "model,audio_seconds,n_requests,cost_usd\n"
        "nova-3,3600.0,5,0.180\n"  # provider: 1 hour, $0.18
    )
    records = [
        # VG: 50 minutes (= 3000 seconds), $0.150
        {"model_id": "deepgram/nova-3", "modality": "stt",
         "input_units": 50.0, "cost_usd": 0.150},
    ]
    lines = reconcile.reconcile("deepgram", records, path)
    assert len(lines) == 1
    line = lines[0]
    assert line.vg_units == pytest.approx(3000.0, abs=0.1)
    assert line.provider_units == pytest.approx(3600.0, abs=0.1)
    assert line.units_diff_abs == pytest.approx(600.0, abs=0.1)
    assert line.units_diff_pct == pytest.approx(16.666, abs=0.01)
    assert line.cost_diff_abs == pytest.approx(0.030, abs=0.001)


def test_reconcile_model_only_in_provider(tmp_path):
    """Model in provider file but absent from VG records: still a row."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o,1000,500,1,0.005\n"
    )
    lines = reconcile.reconcile("openai", [], path)
    assert len(lines) == 1
    assert lines[0].matched_in_vg is False
    assert lines[0].matched_in_provider is True
    assert lines[0].vg_cost == 0.0


def test_reconcile_model_only_in_vg(tmp_path):
    """Model in VG records but absent from provider file: still a row."""
    path = tmp_path / "openai.csv"
    path.write_text("model,input_tokens,output_tokens,n_requests,cost_usd\n")
    records = [
        {"model_id": "openai/gpt-4o-mini", "modality": "llm",
         "input_units": 100, "output_units": 50, "cost_usd": 0.0001},
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    assert lines[0].matched_in_vg is True
    assert lines[0].matched_in_provider is False


def test_format_text_includes_header_and_columns():
    lines = [
        reconcile.ReconcileLine(
            model="nova-3", vg_units=3000.0, provider_units=3600.0,
            units_diff_abs=600.0, units_diff_pct=16.667,
            vg_cost=0.150, provider_cost=0.180,
            cost_diff_abs=0.030, cost_diff_pct=16.667,
            matched_in_vg=True, matched_in_provider=True,
        ),
    ]
    out = reconcile.format_text(lines, "deepgram")
    assert "Model" in out
    assert "nova-3" in out
    assert "audio_s" in out  # the unit label for deepgram


def test_format_text_unknown_provider_falls_back_to_units_label():
    """An unsupported provider gets a generic `units` header rather than KeyError."""
    lines = [
        reconcile.ReconcileLine(
            model="some-model", vg_units=0.0, provider_units=0.0,
            units_diff_abs=0.0, units_diff_pct=0.0,
            vg_cost=0.0, provider_cost=0.0,
            cost_diff_abs=0.0, cost_diff_pct=0.0,
            matched_in_vg=True, matched_in_provider=True,
        ),
    ]
    out = reconcile.format_text(lines, "anthropic")
    assert "VG units" in out  # generic fallback label


def test_parse_provider_file_handles_empty_cost_string(tmp_path):
    """An empty `cost_usd` cell should not crash float() — guarded by `or 0`."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,\n"  # empty cost cell
    )
    parsed = reconcile.parse_provider_file("openai", path)
    assert parsed["gpt-4o-mini"]["cost"] == 0.0


def test_format_csv_writes_diff_rows():
    lines = [
        reconcile.ReconcileLine(
            model="gpt-4o-mini", vg_units=1500.0, provider_units=1500.0,
            units_diff_abs=0.0, units_diff_pct=0.0,
            vg_cost=0.001, provider_cost=0.001,
            cost_diff_abs=0.0, cost_diff_pct=0.0,
            matched_in_vg=True, matched_in_provider=True,
        ),
    ]
    out = reconcile.format_csv(lines)
    assert "model,vg_units,provider_units" in out
    assert "gpt-4o-mini" in out


def test_format_json_writes_array_of_records():
    lines = [
        reconcile.ReconcileLine(
            model="sonic-3", vg_units=1000.0, provider_units=1000.0,
            units_diff_abs=0.0, units_diff_pct=0.0,
            vg_cost=0.012, provider_cost=0.012,
            cost_diff_abs=0.0, cost_diff_pct=0.0,
            matched_in_vg=True, matched_in_provider=True,
        ),
    ]
    payload = json.loads(reconcile.format_json(lines))
    assert isinstance(payload, list)
    assert payload[0]["model"] == "sonic-3"
