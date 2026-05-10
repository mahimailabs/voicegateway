"""Unit tests for v0.1.1 TUI widgets.

Focuses on the pure-Python formatting helpers (date/time parsing,
duration rendering, dict-to-row formatting). Pilot-level rendering
is covered when the consuming screen lands its own test file.
"""

from __future__ import annotations

from voicegateway.cli.tui.widgets.cost_card import CostCard
from voicegateway.cli.tui.widgets.session_row import (
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
    """The Sessions screen relies on Enter focusing a row to push the
    per-turn detail modal (Phase 3 bullet 4); ``can_focus`` has to be
    True for the keyboard event to reach the widget.
    """
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
    """When the response lacks a modality, render `--` rather than
    crash. The Costs screen must still mount cleanly even on a
    fresh install with zero traffic.
    """
    card = CostCard({"total": 0.0, "by_modality": {}})
    assert "--" in card._format_modality("stt")
    assert "--" in card._format_modality("llm")
    assert "--" in card._format_modality("tts")


def test_cost_card_format_modality_handles_no_by_modality_key() -> None:
    """The pre-v0.1.1 daemon response shape may not carry by_modality;
    the card should still render the totals row plus per-modality `--`.
    """
    card = CostCard({"total": 1.23})
    assert "--" in card._format_modality("stt")


def test_cost_card_update_costs_replaces_dict_state() -> None:
    """``update_costs`` swaps the in-memory dict so ``_format_*``
    helpers reflect the new values. The Costs screen relies on this
    for time-range changes and the polling refresh. Safe to call
    pre-mount: the dict update always succeeds and the next
    compose() picks up the new values.
    """
    card = CostCard({"total": 0.001})
    assert "$0.0010" in card._format_total()
    card.update_costs({"total": 9.99, "period": "this_month"})
    assert "$9.9900" in card._format_total()
    assert "this_month" in card._format_total()
    # The in-place re-render is gated on the widget being mounted; on
    # a bare instance the dict mutation alone is enough, no exception.
    assert card._costs["total"] == 9.99
