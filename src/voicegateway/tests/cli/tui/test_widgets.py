"""Unit tests for v0.1.1 TUI widgets."""

from __future__ import annotations

from voicegateway.cli.tui.widgets.cost_card_widget import CostCard, stale_marker
from voicegateway.cli.tui.widgets.log_tail_widget import (
    LogTail,
    format_entry,
    format_timestamp,
)
from voicegateway.cli.tui.widgets.provider_row_widget import (
    ProviderRow,
    format_status,
)
from voicegateway.cli.tui.widgets.session_row_widget import (
    SessionRow,
    format_duration,
    format_time,
)

# ---------------------------------------------------------------------------
# format_time
# ---------------------------------------------------------------------------


def test_format_time_renders_hh_mm_ss_from_iso() -> None:
    assert format_time("2026-05-10T16:23:05.371824+00:00") == "16:23:05"


def test_format_time_returns_dashes_on_empty() -> None:
    assert format_time("") == "--"


def test_format_time_returns_dashes_on_garbage() -> None:
    assert format_time("not-a-timestamp") == "--"


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


def test_format_duration_seconds() -> None:
    start = "2026-05-10T16:23:05+00:00"
    end = "2026-05-10T16:23:42+00:00"
    assert format_duration(start, end) == "37s"


def test_format_duration_minutes() -> None:
    start = "2026-05-10T16:23:00+00:00"
    end = "2026-05-10T16:25:30+00:00"
    assert format_duration(start, end) == "2m30s"


def test_format_duration_hours() -> None:
    start = "2026-05-10T15:00:00+00:00"
    end = "2026-05-10T16:30:00+00:00"
    assert format_duration(start, end) == "1h30m"


def test_format_duration_zero_when_end_before_start() -> None:
    """Out-of-order timestamps clamp to zero rather than negative."""
    start = "2026-05-10T16:25:00+00:00"
    end = "2026-05-10T16:24:00+00:00"
    assert format_duration(start, end) == "0s"


def test_format_duration_returns_dashes_on_missing_bound() -> None:
    assert format_duration("", "2026-05-10T16:23:05+00:00") == "--"
    assert format_duration("2026-05-10T16:23:05+00:00", "") == "--"


# ---------------------------------------------------------------------------
# SessionRow rendering
# ---------------------------------------------------------------------------


def _session_fixture() -> dict[str, object]:
    return {
        "id": "vg-s1",
        "project": "default",
        "started_at": "2026-05-10T16:23:05+00:00",
        "ended_at": "2026-05-10T16:23:42+00:00",
        "total_cost_usd": 0.0123,
        "providers": ["openai", "deepgram"],
        "modalities": ["llm", "stt"],
        "request_count": 4,
    }


def test_session_row_exposes_session_id() -> None:
    row = SessionRow(_session_fixture())
    assert row.session_id == "vg-s1"


def test_session_row_renders_all_four_fields() -> None:
    row = SessionRow(_session_fixture())
    rendered = str(row.renderable)
    assert "16:23:05" in rendered
    assert "37s" in rendered
    assert "$0.0123" in rendered
    assert "openai, deepgram" in rendered


def test_session_row_handles_empty_providers() -> None:
    fix = _session_fixture()
    fix["providers"] = []
    row = SessionRow(fix)
    rendered = str(row.renderable)
    # No trailing crash; the providers slot just collapses to empty.
    assert "16:23:05" in rendered


def test_session_row_handles_missing_cost() -> None:
    fix = _session_fixture()
    fix["total_cost_usd"] = None
    row = SessionRow(fix)
    rendered = str(row.renderable)
    assert "$0.0000" in rendered


def test_session_row_update_replaces_dict_and_rerenders() -> None:
    row = SessionRow(_session_fixture())
    new = _session_fixture()
    new["id"] = "vg-s2"
    new["total_cost_usd"] = 1.2345
    row.update_session(new)
    assert row.session_id == "vg-s2"
    assert "$1.2345" in str(row.renderable)


def test_session_row_is_focusable() -> None:
    """The Sessions screen relies on Enter focusing a row to push the"""
    row = SessionRow(_session_fixture())
    assert row.can_focus is True


# ---------------------------------------------------------------------------
# CostCard formatting (REQ-VG-TUI-003)
# ---------------------------------------------------------------------------


def _costs_fixture() -> dict[str, object]:
    return {
        "period": "today",
        "total": 0.0524,
        "by_modality": {
            "stt": {"cost": 0.0123, "request_count": 4},
            "llm": {"cost": 0.0301, "request_count": 7},
            "tts": {"cost": 0.0100, "request_count": 3},
        },
    }


def test_cost_card_format_total_renders_period_and_dollars() -> None:
    card = CostCard(_costs_fixture())
    text = card._format_total()
    assert "today" in text
    assert "$0.0524" in text


def test_cost_card_format_total_default_period_is_today() -> None:
    card = CostCard({"total": 0.0})
    assert "today" in card._format_total()


def test_cost_card_format_total_handles_none_total() -> None:
    """A None total renders as $0.0000 rather than crashing."""
    card = CostCard({"total": None, "period": "this_week"})
    text = card._format_total()
    assert "this_week" in text
    assert "$0.0000" in text


def test_cost_card_format_modality_renders_each() -> None:
    card = CostCard(_costs_fixture())
    assert "STT" in card._format_modality("stt")
    assert "$0.0123" in card._format_modality("stt")
    assert "4 requests" in card._format_modality("stt")
    assert "LLM" in card._format_modality("llm")
    assert "$0.0301" in card._format_modality("llm")
    assert "TTS" in card._format_modality("tts")


def test_cost_card_format_modality_handles_missing_by_modality() -> None:
    """When the response lacks a modality, render `--` rather than"""
    card = CostCard({"total": 0.0, "by_modality": {}})
    assert "--" in card._format_modality("stt")
    assert "--" in card._format_modality("llm")
    assert "--" in card._format_modality("tts")


def test_cost_card_format_modality_handles_no_by_modality_key() -> None:
    """The pre-v0.1.1 daemon response shape may not carry by_modality;"""
    card = CostCard({"total": 1.23})
    assert "--" in card._format_modality("stt")


def test_cost_card_update_costs_replaces_dict_state() -> None:
    """``update_costs`` swaps the in-memory dict so ``_format_*``"""
    card = CostCard({"total": 0.001})
    assert "$0.0010" in card._format_total()
    card.update_costs({"total": 9.99, "period": "this_month"})
    assert "$9.9900" in card._format_total()
    assert "this_month" in card._format_total()
    # The in-place re-render is gated on the widget being mounted; on
    # a bare instance the dict mutation alone is enough, no exception.
    assert card._costs["total"] == 9.99


# ---------------------------------------------------------------------------
# stale_marker (REQ-VG-TUI-003 freshness indicator)
# ---------------------------------------------------------------------------


def test_stale_marker_flags_old_iso_date() -> None:
    """A pricing stamp older than 24 h surfaces the (as of X) marker."""
    marker = stale_marker("voicegateway-catalog@2025-01-01")
    assert marker == "  (as of 2025-01-01)"


def test_stale_marker_silent_on_recent_date() -> None:
    """Today's date is not stale; no marker."""
    from datetime import date as _date

    today = _date.today().isoformat()
    assert stale_marker(f"voicegateway-catalog@{today}") == ""


def test_stale_marker_silent_on_version_token() -> None:
    """genai-prices stamps the LLM source as ``genai-prices@0.0.57``;"""
    assert stale_marker("genai-prices@0.0.57") == ""


def test_stale_marker_silent_on_bare_or_missing() -> None:
    assert stale_marker(None) == ""
    assert stale_marker("") == ""
    assert stale_marker("no-at-symbol") == ""


def test_stale_marker_silent_on_non_string() -> None:
    """Some catalog versions returned a dict; tolerate it as 'unknown'."""
    assert stale_marker({"date": "2025-01-01"}) == ""
    assert stale_marker(123) == ""


def test_cost_card_modality_carries_stale_marker() -> None:
    """``_format_modality`` appends the (as of X) suffix when the"""
    costs = {
        "total": 0.05,
        "by_modality": {
            "stt": {"cost": 0.01, "request_count": 4},
            "llm": {"cost": 0.03, "request_count": 7},
        },
        "pricing_sources": {
            "stt": "voicegateway-catalog@2025-01-01",  # stale
            "llm": "genai-prices@0.0.57",  # version, no age
        },
    }
    card = CostCard(costs)
    stt_line = card._format_modality("stt")
    assert "(as of 2025-01-01)" in stt_line
    llm_line = card._format_modality("llm")
    assert "(as of" not in llm_line


def test_cost_card_modality_no_marker_when_fresh() -> None:
    from datetime import date as _date

    today = _date.today().isoformat()
    costs = {
        "by_modality": {"stt": {"cost": 0.01, "request_count": 1}},
        "pricing_sources": {
            "stt": f"voicegateway-catalog@{today}",
        },
    }
    card = CostCard(costs)
    assert "(as of" not in card._format_modality("stt")


# ---------------------------------------------------------------------------
# LogTail formatters (REQ-VG-TUI-004)
# ---------------------------------------------------------------------------


def _log_entry_fixture() -> dict[str, object]:
    return {
        "id": "r1",
        "timestamp": 1715356800.0,  # 2024-05-10T17:20:00 UTC
        "modality": "llm",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "project": "default",
        "cost_usd": 0.0123,
        "status": "success",
        "total_latency_ms": 145.7,
    }


def test_format_timestamp_epoch_renders_hh_mm_ss() -> None:
    """A real epoch float renders as HH:MM:SS in local time."""
    rendered = format_timestamp(1715356800.0)
    assert ":" in rendered
    assert len(rendered) == 8  # HH:MM:SS


def test_format_timestamp_int_works() -> None:
    rendered = format_timestamp(1715356800)
    assert ":" in rendered


def test_format_timestamp_iso_string() -> None:
    """Some daemon shapes return ISO strings."""
    rendered = format_timestamp("2026-05-10T16:23:05+00:00")
    assert rendered == "16:23:05"


def test_format_timestamp_none_returns_dashes() -> None:
    assert format_timestamp(None) == "--:--:--"


def test_format_timestamp_garbage_returns_dashes() -> None:
    assert format_timestamp("not-a-timestamp") == "--:--:--"
    assert format_timestamp(["unexpected", "type"]) == "--:--:--"


def test_format_entry_renders_every_field() -> None:
    rendered = format_entry(_log_entry_fixture())
    assert "LLM" in rendered
    assert "openai" in rendered
    assert "gpt-4o-mini" in rendered
    assert "$0.0123" in rendered
    assert "145ms" in rendered  # int-cast of 145.7
    assert "success" in rendered


def test_format_entry_falls_back_to_ttfb_when_total_missing() -> None:
    entry = _log_entry_fixture()
    entry.pop("total_latency_ms")
    entry["ttfb_ms"] = 88.0
    rendered = format_entry(entry)
    assert "88ms" in rendered


def test_format_entry_handles_missing_fields() -> None:
    """A nearly-empty entry still renders without crashing."""
    rendered = format_entry({"id": "x"})
    assert "?" in rendered
    assert "$0.0000" in rendered
    assert "0ms" in rendered


def test_format_entry_handles_garbled_latency() -> None:
    entry = _log_entry_fixture()
    entry["total_latency_ms"] = "not-a-number"
    rendered = format_entry(entry)
    assert "0ms" in rendered  # falls back rather than crashing


# ---------------------------------------------------------------------------
# LogTail filter behaviour (REQ-VG-TUI-004 ``/`` filter)
# ---------------------------------------------------------------------------


def _entries_fixture() -> list[dict[str, object]]:
    return [
        {
            "id": "r1",
            "timestamp": 1715356800.0,
            "modality": "llm",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
            "cost_usd": 0.001,
            "status": "success",
            "total_latency_ms": 100,
        },
        {
            "id": "r2",
            "timestamp": 1715356860.0,
            "modality": "stt",
            "provider": "deepgram",
            "model_id": "nova-3",
            "cost_usd": 0.0005,
            "status": "success",
            "total_latency_ms": 80,
        },
        {
            "id": "r3",
            "timestamp": 1715356920.0,
            "modality": "tts",
            "provider": "cartesia",
            "model_id": "sonic-2",
            "cost_usd": 0.0008,
            "status": "success",
            "total_latency_ms": 90,
        },
    ]


def test_log_tail_retains_all_entries_via_append() -> None:
    """``_all_entries`` accumulates every appended row so set_filter"""
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    assert len(tail._all_entries) == 3
    assert {e["id"] for e in tail._all_entries} == {"r1", "r2", "r3"}


def test_log_tail_dedups_by_id_across_appends() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    # Re-append the same batch -- de-dup should drop them.
    tail.append_entries(_entries_fixture())
    assert len(tail._all_entries) == 3


def test_log_tail_set_filter_substring_match() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    tail.set_filter("deepgram")
    assert tail._filter == "deepgram"
    # All-entries unchanged; only the rendered buffer was mutated.
    assert len(tail._all_entries) == 3


def test_log_tail_set_filter_case_insensitive() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    tail.set_filter("OpenAI")
    assert tail._filter == "OpenAI"
    # ``_matches_filter`` lowercases both sides, so this still
    # matches the openai row.
    assert tail._matches_filter(_entries_fixture()[0]) is True


def test_log_tail_set_filter_empty_string_clears() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    tail.set_filter("openai")
    tail.set_filter("")
    assert tail._filter is None


def test_log_tail_set_filter_none_clears() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    tail.set_filter("openai")
    tail.set_filter(None)
    assert tail._filter is None


def test_log_tail_reset_clears_state() -> None:
    tail = LogTail()
    tail.append_entries(_entries_fixture())
    tail.set_filter("openai")
    tail.reset()
    assert tail._filter is None
    assert tail._all_entries == []
    assert tail._seen_ids == set()


def test_log_tail_append_skips_non_matching_under_active_filter() -> None:
    """Real-time append while a filter is set: matching rows go to"""
    tail = LogTail()
    tail.set_filter("openai")
    tail.append_entries(_entries_fixture())
    # All three rows retained; only one matches.
    assert len(tail._all_entries) == 3
    matches = [e for e in tail._all_entries if tail._matches_filter(e)]
    assert len(matches) == 1
    assert matches[0]["provider"] == "openai"


# ---------------------------------------------------------------------------
# ProviderRow + format_status (REQ-VG-TUI-005)
# ---------------------------------------------------------------------------


def _provider_fixture(
    *,
    provider_id: str = "openai",
    project: str | None = "default",
    status: str | None = "ok",
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "provider_type": "openai",
        "project": project,
        "status": status,
    }


def test_format_status_renders_ok_marker() -> None:
    assert "[ok]" in format_status("ok")


def test_format_status_renders_fail_marker() -> None:
    assert "[fail]" in format_status("fail")


def test_format_status_renders_untested_for_explicit_value() -> None:
    assert "[?]" in format_status("untested")


def test_format_status_handles_none_and_unknown() -> None:
    """None and unknown statuses fall through to the untested form"""
    assert "[?]" in format_status(None)
    assert "[?]" in format_status("")
    assert "[?]" in format_status("mystery")
    assert "[?]" in format_status(123)


def test_provider_row_renders_every_field() -> None:
    row = ProviderRow(_provider_fixture(provider_id="openai-prod"))
    rendered = str(row.renderable)
    assert "[ok]" in rendered
    assert "openai-prod" in rendered
    assert "openai" in rendered  # provider_type
    assert "default" in rendered  # project


def test_provider_row_renders_global_label_when_project_missing() -> None:
    """A NULL ``project`` (the v0.0.5 pre-per-project rows) renders"""
    row = ProviderRow(_provider_fixture(project=None))
    assert "(global)" in str(row.renderable)


def test_provider_row_exposes_provider_id() -> None:
    row = ProviderRow(_provider_fixture(provider_id="my-key"))
    assert row.provider_id == "my-key"


def test_provider_row_exposes_normalised_status() -> None:
    """``.status`` returns the normalised value ``ok`` / ``fail`` /"""
    assert ProviderRow(_provider_fixture(status="ok")).status == "ok"
    assert ProviderRow(_provider_fixture(status="fail")).status == "fail"
    assert ProviderRow(_provider_fixture(status=None)).status == "untested"
    assert ProviderRow(_provider_fixture(status="mystery")).status == "untested"


def test_provider_row_carries_status_css_class() -> None:
    """Phase 8's TCSS pass styles ``status-ok`` etc. green / red /"""
    assert "status-ok" in ProviderRow(_provider_fixture(status="ok")).classes
    assert "status-fail" in ProviderRow(_provider_fixture(status="fail")).classes
    assert "status-untested" in ProviderRow(_provider_fixture(status=None)).classes


def test_provider_row_update_provider_swaps_status_class() -> None:
    """``update_provider`` re-renders the row + swaps the CSS class so"""
    row = ProviderRow(_provider_fixture(status="untested"))
    assert "status-untested" in row.classes
    row.update_provider(_provider_fixture(status="ok"))
    assert "status-ok" in row.classes
    assert "status-untested" not in row.classes
    assert "[ok]" in str(row.renderable)


def test_provider_row_is_focusable() -> None:
    """Phase 6 bullet 3 binds ``t`` -> test_provider on the focused"""
    assert ProviderRow(_provider_fixture()).can_focus is True
