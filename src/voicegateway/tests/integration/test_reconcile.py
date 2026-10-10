"""Smoke tests for `voicegateway/reconcile/core.py` diff math."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicegateway.services import reconciliation_service as reconcile


def _assert_parsed(parsed, expected):
    """``expected`` maps model -> the fields to pin; models must match exactly."""
    assert set(parsed) == set(expected)
    for model, fields in expected.items():
        for field, value in fields.items():
            assert parsed[model][field] == pytest.approx(value, abs=0.0001), (
                model,
                field,
            )


_CARTESIA_JSON = json.dumps(
    [
        {
            "model": "sonic-3",
            "characters": 2_500_000,
            "credits": 250_000,
            "n_requests": 1000,
            "cost_usd": 30.0,
        },
    ]
)


@pytest.mark.parametrize(
    ("provider", "filename", "content", "expected"),
    [
        pytest.param(
            "openai",
            "openai.csv",
            "model,input_tokens,output_tokens,n_requests,cost_usd\n"
            "gpt-4o-mini,1000000,500000,500,0.225\n",
            # units = input + output
            {"gpt-4o-mini": {"units": 1_500_000, "cost": 0.225}},
            id="openai-csv",
        ),
        pytest.param(
            "openai",
            "openai.csv",
            "model,input_tokens,output_tokens,n_requests,cost_usd\n"
            "gpt-4o-mini,1000000,500000,500,0.225\n"
            "gpt-4o,200000,100000,200,0.900\n"
            "gpt-4-turbo,50000,25000,80,1.200\n",
            {
                "gpt-4o-mini": {"units": 1_500_000},
                "gpt-4o": {"units": 300_000, "n_requests": 200},
                "gpt-4-turbo": {"units": 75_000, "cost": 1.200},
            },
            id="openai-csv-multiple-models",
        ),
        pytest.param(
            "openai",
            "openai.csv",
            # n_requests and cost_usd absent: the `row.get(..., 0) or 0`
            # fallback must yield zero, not KeyError.
            "model,input_tokens,output_tokens\ngpt-4o-mini,1000,500\n",
            {"gpt-4o-mini": {"units": 1500, "cost": 0.0, "n_requests": 0.0}},
            id="openai-csv-missing-columns",
        ),
        pytest.param(
            "openai",
            "openai.csv",
            # An empty cost cell must not crash float().
            "model,input_tokens,output_tokens,n_requests,cost_usd\n"
            "gpt-4o-mini,1000,500,1,\n",
            {"gpt-4o-mini": {"cost": 0.0}},
            id="openai-csv-empty-cost-cell",
        ),
        pytest.param(
            "deepgram",
            "deepgram.csv",
            # Units are audio_seconds, never converted to minutes.
            "model,audio_seconds,n_requests,cost_usd\nnova-3,180000.0,1500,8.700\n",
            {"nova-3": {"units": 180000.0, "cost": 8.700}},
            id="deepgram-csv",
        ),
        pytest.param(
            "deepgram",
            "deepgram.csv",
            "model,audio_seconds,n_requests,cost_usd\n"
            "nova-3,180000.0,1500,8.700\n"
            "nova-2,90000.0,800,4.350\n"
            "flux-general,30000.0,250,1.450\n",
            {
                "nova-3": {"units": 180000.0},
                "nova-2": {"units": 90000.0, "n_requests": 800},
                "flux-general": {"units": 30000.0, "cost": 1.450},
            },
            id="deepgram-csv-multiple-models",
        ),
        pytest.param(
            "deepgram",
            "deepgram.csv",
            "model,audio_seconds\nnova-3,12000.0\n",
            {"nova-3": {"units": 12000.0, "cost": 0.0, "n_requests": 0.0}},
            id="deepgram-csv-missing-columns",
        ),
        pytest.param(
            "cartesia",
            "cartesia.json",
            _CARTESIA_JSON,
            {"sonic-3": {"units": 2_500_000}},
            id="cartesia-json",
        ),
        pytest.param(
            "elevenlabs",
            "elevenlabs.json",
            _CARTESIA_JSON,
            {"sonic-3": {"units": 2_500_000}},
            id="elevenlabs-json",
        ),
        pytest.param(
            "cartesia",
            "cartesia.csv",
            # characters and credits differ on purpose: units must come
            # from `characters`, not `credits`.
            "model,characters,credits,n_requests,cost_usd\n"
            "sonic-3,2500000,250000,1000,30.0\n",
            {"sonic-3": {"units": 2_500_000, "cost": 30.0, "n_requests": 1000}},
            id="cartesia-csv",
        ),
        pytest.param(
            "cartesia",
            "cartesia.csv",
            "model,characters,n_requests,cost_usd\n"
            "sonic-3,2500000,1000,30.0\n"
            "sonic-turbo,800000,400,9.6\n",
            {
                "sonic-3": {"units": 2_500_000},
                "sonic-turbo": {"units": 800_000, "cost": 9.6},
            },
            id="cartesia-csv-multiple-models",
        ),
        pytest.param(
            "cartesia",
            "cartesia.csv",
            "model,characters\nsonic-3,500000\n",
            {"sonic-3": {"units": 500_000, "cost": 0.0, "n_requests": 0.0}},
            id="cartesia-csv-missing-columns",
        ),
    ],
)
def test_parse_provider_file(tmp_path, provider, filename, content, expected):
    path = tmp_path / filename
    path.write_text(content)
    _assert_parsed(reconcile.parse_provider_file(provider, path), expected)


# Sample fixtures committed under tests/fixtures/usage_exports/
# anchor the parser against committed reference data rather than
# inline strings. This double-checks the canonical schema against
# real files (matters when a future docs change drifts the schema
# and the parser does not). Updating a fixture requires updating these.
_FIXTURES_USAGE_DIR = Path(__file__).parent.parent / "fixtures" / "usage_exports"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (
            "openai",
            {
                "gpt-4o-mini": {"units": 3_750_000, "cost": 0.5625},  # 2.5M + 1.25M
                "gpt-4o": {"units": 750_000},
                "gpt-4-turbo": {"n_requests": 250},
            },
        ),
        (
            "deepgram",
            {
                "nova-3": {"units": 180_000.0},
                "nova-2": {"units": 90_000.0},
                "flux-general": {"cost": 1.450},
            },
        ),
        (
            "cartesia",
            {
                "sonic-3": {"units": 2_500_000},
                "sonic-turbo": {"units": 800_000, "cost": 9.600},
            },
        ),
    ],
)
def test_parse_provider_file_loads_committed_sample(provider, expected):
    parsed = reconcile.parse_provider_file(
        provider, _FIXTURES_USAGE_DIR / f"{provider}-sample.csv"
    )
    _assert_parsed(parsed, expected)


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
        {
            "model_id": "deepgram/nova-3",
            "modality": "stt",
            "input_units": 1.0,
            "cost_usd": 0.005,
        },
        {
            "model_id": "deepgram/nova-3",
            "modality": "stt",
            "input_units": 2.5,
            "cost_usd": 0.0125,
        },
    ]
    agg = reconcile.aggregate_vg_records("deepgram", records)
    assert agg["nova-3"]["units"] == pytest.approx(3.5 * 60, abs=0.1)
    assert agg["nova-3"]["cost"] == pytest.approx(0.0175, abs=0.001)


def test_aggregate_vg_records_elevenlabs_uses_tts_character_units():
    records = [
        {
            "model_id": "elevenlabs/eleven_turbo_v2_5",
            "modality": "tts",
            "input_units": 500,
            "cost_usd": 0.05,
        },
        {
            "model_id": "elevenlabs/eleven_turbo_v2_5",
            "modality": "llm",
            "input_units": 250,
            "cost_usd": 0.02,
        },
    ]

    agg = reconcile.aggregate_vg_records("elevenlabs", records)

    assert agg["eleven_turbo_v2_5"]["units"] == 500.0
    assert agg["eleven_turbo_v2_5"]["cost"] == pytest.approx(0.05)


def test_aggregate_vg_records_filters_other_providers():
    """Records from other providers are skipped."""
    records = [
        {
            "model_id": "deepgram/nova-3",
            "modality": "stt",
            "input_units": 1.0,
            "cost_usd": 0.005,
        },
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 100,
            "cost_usd": 0.001,
        },
    ]
    agg = reconcile.aggregate_vg_records("deepgram", records)
    assert "gpt-4o-mini" not in agg
    assert agg["nova-3"]["units"] == pytest.approx(60.0, abs=0.1)


def test_aggregate_vg_records_filters_other_modalities():
    """Records with the right provider but wrong modality are skipped."""
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.001,
        },
        {
            "model_id": "openai/whisper-1",
            "modality": "stt",
            "input_units": 5.0,
            "cost_usd": 0.030,
        },
        {
            "model_id": "openai/tts-1",
            "modality": "tts",
            "input_units": 200,
            "cost_usd": 0.003,
        },
    ]
    agg = reconcile.aggregate_vg_records("openai", records)
    assert set(agg.keys()) == {"gpt-4o-mini"}
    assert agg["gpt-4o-mini"]["units"] == 1500.0
    assert agg["gpt-4o-mini"]["cost"] == pytest.approx(0.001, abs=0.0001)


def test_aggregate_vg_records_openai_sums_input_and_output():
    """OpenAI: VG units = input_tokens + output_tokens, matching the canonical file."""
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.001,
        },
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
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.001,
        },
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
        {
            "model_id": "deepgram/nova-3",
            "modality": "stt",
            "input_units": 50.0,
            "cost_usd": 0.150,
        },
    ]
    lines = reconcile.reconcile("deepgram", records, path)
    assert len(lines) == 1
    line = lines[0]
    assert line.vg_units == pytest.approx(3000.0, abs=0.1)
    assert line.provider_units == pytest.approx(3600.0, abs=0.1)
    assert line.units_diff_abs == pytest.approx(600.0, abs=0.1)
    assert line.units_diff_pct == pytest.approx(16.666, abs=0.01)
    assert line.cost_diff_abs == pytest.approx(0.030, abs=0.001)


def test_reconcile_currency_precision_sub_cent(tmp_path):
    """Sub-cent diffs round-trip through reconcile without precision loss."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        # Provider: $0.00010500
        "gpt-4o-mini,7000,3500,7,0.00010500\n"
    )
    records = [
        # VG: $0.00010000 — a 5-microcent gap (~5%).
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 7000,
            "output_units": 3500,
            "cost_usd": 0.00010000,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    line = lines[0]
    # The diff is $0.000005 — sub-cent precision must survive.
    assert line.cost_diff_abs == pytest.approx(0.000005, abs=1e-9), (
        f"sub-cent diff lost precision; got {line.cost_diff_abs!r}"
    )
    assert line.cost_diff_pct == pytest.approx(4.7619, abs=0.01)


def test_reconcile_currency_precision_high_volume(tmp_path):
    """Million-dollar volumes preserve cent-level precision."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,5000000000,2500000000,1000000,1234567.89\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 5000000000,
            "output_units": 2500000000,
            "cost_usd": 1234566.66,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    line = lines[0]
    # Provider $1234567.89 - VG $1234566.66 = $1.23 drift.
    assert line.cost_diff_abs == pytest.approx(1.23, abs=0.01)
    # ~0.0001% drift; well within the 5% threshold.
    assert abs(line.cost_diff_pct) < 0.001
    assert line.flagged is False


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
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 100,
            "output_units": 50,
            "cost_usd": 0.0001,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    assert lines[0].matched_in_vg is True
    assert lines[0].matched_in_provider is False


def test_format_text_surfaces_no_provider_data_label(tmp_path):
    """When VG has data but provider does not, the rendered row must"""
    path = tmp_path / "openai.csv"
    # Empty provider file (header only).
    path.write_text("model,input_tokens,output_tokens,n_requests,cost_usd\n")
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 100,
            "output_units": 50,
            "cost_usd": 0.0001,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    text = reconcile.format_text(lines, "openai")
    assert "no provider data" in text, (
        f"missing-provider label not rendered; got:\n{text}"
    )
    # The misleading "(prov-missing)" stub from the previous label
    # must be gone.
    assert "(prov-missing)" not in text


def test_format_text_includes_total_row(tmp_path):
    """A Total row sums vg_cost and provider_cost across matched-both rows."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
        "gpt-4o,500,250,1,2.00\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.94,
        },
        {
            "model_id": "openai/gpt-4o",
            "modality": "llm",
            "input_units": 500,
            "output_units": 250,
            "cost_usd": 1.95,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    text = reconcile.format_text(lines, "openai")
    assert "Total" in text
    # Total VG: 0.94 + 1.95 = 2.89; Total provider: 1.00 + 2.00 = 3.00
    assert "$   2.8900" in text or "$2.8900" in text
    assert "$   3.0000" in text or "$3.0000" in text
    # Total diff +0.11; pct = 0.11/3.00 = ~3.67%
    assert "+0.1100" in text


def test_format_text_total_excludes_missing_side_rows(tmp_path):
    """Rows with only one side of data must not contribute to the Total."""
    path = tmp_path / "openai.csv"
    # Provider has gpt-4o-mini AND a never-seen-in-VG model.
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
        "gpt-3.5-turbo,500,250,1,5.00\n"
    )
    records = [
        # VG only logs gpt-4o-mini.
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.97,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    text = reconcile.format_text(lines, "openai")
    # Missing-side row appears in the body with the (no vg data) flag.
    assert "no vg data" in text
    # Total row sums only the matched-both row: $0.97 vs $1.00.
    # gpt-3.5-turbo's $5.00 must NOT be added to provider total.
    # Diff $0.03 expected, NOT $4.03.
    assert "+0.0300" in text
    assert "+4.0300" not in text


def test_format_text_colorize_wraps_flagged_rows():
    """When colorize=True, flagged rows are wrapped in ANSI yellow."""
    line = reconcile.ReconcileLine(
        model="gpt-4o-mini",
        vg_units=1000.0,
        provider_units=1000.0,
        units_diff_abs=0.0,
        units_diff_pct=0.0,
        vg_cost=0.94,
        provider_cost=1.00,
        cost_diff_abs=0.06,
        cost_diff_pct=6.0,
        matched_in_vg=True,
        matched_in_provider=True,
        flagged=True,
    )
    text = reconcile.format_text([line], "openai", colorize=True)
    assert "\033[33m" in text  # yellow
    assert "\033[0m" in text  # reset


def test_format_text_no_color_default():
    """colorize default is False; no ANSI codes appear in output."""
    line = reconcile.ReconcileLine(
        model="gpt-4o-mini",
        vg_units=1000.0,
        provider_units=1000.0,
        units_diff_abs=0.0,
        units_diff_pct=0.0,
        vg_cost=0.94,
        provider_cost=1.00,
        cost_diff_abs=0.06,
        cost_diff_pct=6.0,
        matched_in_vg=True,
        matched_in_provider=True,
        flagged=True,
    )
    text = reconcile.format_text([line], "openai")
    assert "\033[" not in text


def test_format_text_flagged_row_marked_with_asterisk():
    """Flagged rows are tagged with ' *' even without colorize."""
    line = reconcile.ReconcileLine(
        model="gpt-4o-mini",
        vg_units=1000.0,
        provider_units=1000.0,
        units_diff_abs=0.0,
        units_diff_pct=0.0,
        vg_cost=0.94,
        provider_cost=1.00,
        cost_diff_abs=0.06,
        cost_diff_pct=6.0,
        matched_in_vg=True,
        matched_in_provider=True,
        flagged=True,
    )
    text = reconcile.format_text([line], "openai")
    assert " *" in text
    assert "1 flagged row" in text


def test_format_text_surfaces_no_vg_data_label(tmp_path):
    """Symmetric to 4.3 #2: when provider has data but VG does not,"""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,0.005\n"
    )
    # Empty VG records.
    lines = reconcile.reconcile("openai", [], path)
    text = reconcile.format_text(lines, "openai")
    assert "no vg data" in text, f"missing-vg label not rendered; got:\n{text}"
    # Prior "(vg-missing)" jargon must be gone.
    assert "(vg-missing)" not in text


def test_reconcile_flags_lines_above_threshold(tmp_path):
    """Default 5% threshold flips ReconcileLine.flagged when |cost_diff_pct| > 5."""
    path = tmp_path / "openai.csv"
    # provider reports $1.00; VG would compute $0.94 -> -6% diff > 5%.
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.94,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    line = lines[0]
    assert abs(line.cost_diff_pct) > 5.0
    assert line.flagged is True


def test_reconcile_does_not_flag_within_threshold(tmp_path):
    """A 3% drift stays under the default 5% threshold; flagged stays False."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
    )
    # VG cost 0.97 vs provider 1.00 -> +3% drift, under threshold.
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.97,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert lines[0].flagged is False
    assert abs(lines[0].cost_diff_pct) < 5.0


def test_reconcile_threshold_is_configurable(tmp_path):
    """Lowering threshold to 1% flips a 3% drift to flagged."""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.97,
        },
    ]
    lines = reconcile.reconcile("openai", records, path, threshold_pct=1.0)
    assert lines[0].flagged is True


def test_reconcile_flags_zero_provider_cost_with_nonzero_vg(tmp_path):
    """Edge case: provider invoice line is $0 (free tier, credit-covered,"""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,0.00\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.94,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert len(lines) == 1
    line = lines[0]
    assert line.matched_in_vg is True
    assert line.matched_in_provider is True
    assert line.provider_cost == 0.0
    assert line.vg_cost > 0.0
    assert line.cost_diff_abs != 0.0
    assert line.flagged is True


def test_reconcile_does_not_flag_zero_zero_match(tmp_path):
    """Symmetric case: both sides matched and both costs are zero"""
    path = tmp_path / "openai.csv"
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,0.00\n"
    )
    records = [
        {
            "model_id": "openai/gpt-4o-mini",
            "modality": "llm",
            "input_units": 1000,
            "output_units": 500,
            "cost_usd": 0.0,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    assert lines[0].provider_cost == 0.0
    assert lines[0].vg_cost == 0.0
    assert lines[0].flagged is False


def test_reconcile_does_not_flag_missing_sides(tmp_path):
    """Lines missing data on either side carry undefined percent diff;"""
    path = tmp_path / "openai.csv"
    # Provider lists a model VG has no records for.
    path.write_text(
        "model,input_tokens,output_tokens,n_requests,cost_usd\n"
        "gpt-4o-mini,1000,500,1,1.00\n"
    )
    # VG has a different model only.
    records = [
        {
            "model_id": "openai/gpt-4-turbo",
            "modality": "llm",
            "input_units": 100,
            "output_units": 50,
            "cost_usd": 0.50,
        },
    ]
    lines = reconcile.reconcile("openai", records, path)
    by_model = {ln.model: ln for ln in lines}
    assert by_model["gpt-4o-mini"].matched_in_vg is False
    assert by_model["gpt-4o-mini"].flagged is False
    assert by_model["gpt-4-turbo"].matched_in_provider is False
    assert by_model["gpt-4-turbo"].flagged is False


def test_format_text_includes_header_and_columns():
    lines = [
        reconcile.ReconcileLine(
            model="nova-3",
            vg_units=3000.0,
            provider_units=3600.0,
            units_diff_abs=600.0,
            units_diff_pct=16.667,
            vg_cost=0.150,
            provider_cost=0.180,
            cost_diff_abs=0.030,
            cost_diff_pct=16.667,
            matched_in_vg=True,
            matched_in_provider=True,
        ),
    ]
    out = reconcile.format_text(lines, "deepgram")
    assert "Model" in out
    assert "nova-3" in out
    assert "audio_s" in out  # the unit label for deepgram


def test_format_text_labels_elevenlabs_units_as_chars():
    line = reconcile.ReconcileLine(
        model="eleven_turbo_v2_5",
        vg_units=500.0,
        provider_units=500.0,
        units_diff_abs=0.0,
        units_diff_pct=0.0,
        vg_cost=0.05,
        provider_cost=0.05,
        cost_diff_abs=0.0,
        cost_diff_pct=0.0,
        matched_in_vg=True,
        matched_in_provider=True,
    )

    out = reconcile.format_text([line], "elevenlabs")

    assert "VG chars" in out
    assert "Provider chars" in out


def test_format_text_unknown_provider_falls_back_to_units_label():
    """An unsupported provider gets a generic `units` header rather than KeyError."""
    lines = [
        reconcile.ReconcileLine(
            model="some-model",
            vg_units=0.0,
            provider_units=0.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.0,
            provider_cost=0.0,
            cost_diff_abs=0.0,
            cost_diff_pct=0.0,
            matched_in_vg=True,
            matched_in_provider=True,
        ),
    ]
    out = reconcile.format_text(lines, "anthropic")
    assert "VG units" in out  # generic fallback label


def test_format_csv_writes_diff_rows():
    lines = [
        reconcile.ReconcileLine(
            model="gpt-4o-mini",
            vg_units=1500.0,
            provider_units=1500.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.001,
            provider_cost=0.001,
            cost_diff_abs=0.0,
            cost_diff_pct=0.0,
            matched_in_vg=True,
            matched_in_provider=True,
        ),
    ]
    out = reconcile.format_csv(lines)
    assert "model,vg_units,provider_units" in out
    assert "gpt-4o-mini" in out


def test_format_csv_includes_flagged_column():
    """CSV exposes `flagged` so spreadsheets can filter on it without"""
    import csv as _csv
    import io as _io

    lines = [
        reconcile.ReconcileLine(
            model="gpt-4o-mini",
            vg_units=1500.0,
            provider_units=1500.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.94,
            provider_cost=1.00,
            cost_diff_abs=0.06,
            cost_diff_pct=6.0,
            matched_in_vg=True,
            matched_in_provider=True,
            flagged=True,
        ),
        reconcile.ReconcileLine(
            model="gpt-4o",
            vg_units=500.0,
            provider_units=500.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.97,
            provider_cost=1.00,
            cost_diff_abs=0.03,
            cost_diff_pct=3.0,
            matched_in_vg=True,
            matched_in_provider=True,
            flagged=False,
        ),
    ]
    out = reconcile.format_csv(lines)
    reader = _csv.DictReader(_io.StringIO(out))
    rows = list(reader)
    assert "flagged" in reader.fieldnames
    by_model = {r["model"]: r for r in rows}
    # csv writer renders Python booleans as "True" / "False".
    assert by_model["gpt-4o-mini"]["flagged"] == "True"
    assert by_model["gpt-4o"]["flagged"] == "False"


def test_format_json_writes_design_schema():
    """JSON shape per design §2.2: provider, period, rows, total, flagged_count."""
    lines = [
        reconcile.ReconcileLine(
            model="sonic-3",
            vg_units=1000.0,
            provider_units=1000.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.012,
            provider_cost=0.012,
            cost_diff_abs=0.0,
            cost_diff_pct=0.0,
            matched_in_vg=True,
            matched_in_provider=True,
        ),
    ]
    payload = json.loads(
        reconcile.format_json(
            lines,
            provider="cartesia",
            period_start="2026-04-01",
            period_end="2026-04-30",
        )
    )
    assert isinstance(payload, dict)
    assert payload["provider"] == "cartesia"
    assert payload["period"] == {
        "start": "2026-04-01",
        "end": "2026-04-30",
    }
    assert isinstance(payload["rows"], list)
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["model"] == "sonic-3"
    # Total sums matched-both rows: 0.012 each.
    assert payload["total"]["vg_cost"] == pytest.approx(0.012, abs=1e-6)
    assert payload["total"]["provider_cost"] == pytest.approx(0.012, abs=1e-6)
    assert payload["total"]["diff_abs"] == pytest.approx(0.0, abs=1e-6)
    assert payload["total"]["diff_pct"] == pytest.approx(0.0, abs=1e-3)
    assert payload["flagged_count"] == 0


def test_format_json_counts_flagged_rows():
    """flagged_count reflects the number of rows where flagged=True."""
    lines = [
        reconcile.ReconcileLine(
            model="gpt-4o-mini",
            vg_units=1000.0,
            provider_units=1000.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.94,
            provider_cost=1.00,
            cost_diff_abs=0.06,
            cost_diff_pct=6.0,
            matched_in_vg=True,
            matched_in_provider=True,
            flagged=True,
        ),
        reconcile.ReconcileLine(
            model="gpt-4o",
            vg_units=500.0,
            provider_units=500.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=2.00,
            provider_cost=2.00,
            cost_diff_abs=0.0,
            cost_diff_pct=0.0,
            matched_in_vg=True,
            matched_in_provider=True,
            flagged=False,
        ),
    ]
    payload = json.loads(reconcile.format_json(lines, provider="openai"))
    assert payload["flagged_count"] == 1
    assert payload["total"]["vg_cost"] == pytest.approx(2.94, abs=1e-6)
    assert payload["total"]["provider_cost"] == pytest.approx(3.00, abs=1e-6)


def test_format_json_total_excludes_missing_side():
    """Total must sum only matched-both rows, mirroring format_text."""
    lines = [
        reconcile.ReconcileLine(
            model="gpt-4o-mini",
            vg_units=1000.0,
            provider_units=1000.0,
            units_diff_abs=0.0,
            units_diff_pct=0.0,
            vg_cost=0.97,
            provider_cost=1.00,
            cost_diff_abs=0.03,
            cost_diff_pct=3.0,
            matched_in_vg=True,
            matched_in_provider=True,
        ),
        reconcile.ReconcileLine(
            model="gpt-3.5-turbo",
            vg_units=0.0,
            provider_units=500.0,
            units_diff_abs=500.0,
            units_diff_pct=100.0,
            vg_cost=0.0,
            provider_cost=5.0,
            cost_diff_abs=5.0,
            cost_diff_pct=100.0,
            matched_in_vg=False,
            matched_in_provider=True,
        ),
    ]
    payload = json.loads(reconcile.format_json(lines, provider="openai"))
    # Only the matched-both row contributes; provider total is 1.00,
    # NOT 6.00.
    assert payload["total"]["vg_cost"] == pytest.approx(0.97, abs=1e-6)
    assert payload["total"]["provider_cost"] == pytest.approx(1.00, abs=1e-6)


def test_format_json_provider_and_period_optional():
    """When metadata omitted, the corresponding fields render null."""
    payload = json.loads(reconcile.format_json([]))
    assert payload["provider"] is None
    assert payload["period"] == {"start": None, "end": None}
    assert payload["rows"] == []
    assert payload["flagged_count"] == 0
