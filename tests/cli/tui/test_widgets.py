"""Unit tests for v0.1.1 TUI widgets.

Focuses on the pure-Python formatting helpers (date/time parsing,
duration rendering, dict-to-row formatting). Pilot-level rendering
is covered when the consuming screen lands its own test file.
"""

from __future__ import annotations

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
