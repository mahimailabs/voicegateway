"""One row per tool call, and no payload anywhere near it.

Somebody could see that a turn was slow and could not see that a tool took most
of it. On agents that call tools the tool is usually the largest term in a slow
turn, and it was the one term the views could not show: `llm_ttft_ms` and
`tts_ttfb_ms` are both small and both visible, and the multi-second external
call sitting between them was neither.

THE PAYLOAD RULE IS THE LOAD-BEARING ONE. A tool's arguments and results are the
operator's data, and whatever their tools handle is not ours to store. A name
and a duration are a timing measurement; the payload is a disclosure. That
separation is the entire reason this capture can default on, so it is asserted
here rather than promised in a comment.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from voicegateway.models.tool_call_model import ToolCall

_ATTACH = Path(__file__).resolve().parents[2] / "inference" / "session" / "attach.py"


# --------------------------------------------------------------------------
# No payload, asserted three ways
# --------------------------------------------------------------------------


def test_the_row_has_no_field_that_could_hold_a_payload() -> None:
    """The schema itself refuses it, so there is nowhere to put one."""
    fields = set(ToolCall.model_fields)
    forbidden = {"arguments", "args", "result", "results", "output", "payload", "value"}
    assert not (fields & forbidden), f"payload-shaped field on ToolCall: {fields}"


def test_the_capture_never_reads_the_arguments_attribute() -> None:
    """`function_call.arguments` is right there on the SDK event.

    Reading it would be one word, so this asserts the word is absent rather than
    trusting that nobody adds it. The tool NAME is read from the same object,
    which is what makes the omission a decision rather than an oversight.
    """
    src = _ATTACH.read_text()
    handler_start = src.index("def _on_tool_execution_updated")
    handler = src[handler_start : handler_start + 2000]
    # Comments stripped first: the code carries a comment SAYING arguments are
    # never read, and a naive grep matches its own explanation.
    code = "\n".join(
        line for line in handler.splitlines() if not line.strip().startswith("#")
    )
    assert ".arguments" not in code
    assert 'getattr(call, "name"' in code


def test_the_insert_binds_no_payload_column() -> None:
    """The repository cannot write one even if a row somehow carried it."""
    from voicegateway.repository import tool_calls_repository as repo

    sql = str(repo._INSERT)
    for word in ("arguments", "result", "payload", "output"):
        assert word not in sql, f"{word!r} reachable from the insert"


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------


def _outcome_map() -> dict[str, str]:
    """The SDK's status vocabulary mapped to ours, read from the source."""
    src = _ATTACH.read_text()
    line = next(ln for ln in src.splitlines() if "_OUTCOME = {" in ln)
    return eval(line.split("=", 1)[1].strip())  # noqa: S307 - our own literal


def test_completed_failed_and_cancelled_each_map_to_their_own_outcome() -> None:
    """Three terminal states the SDK distinguishes, kept distinct here.

    Collapsing error into cancelled would hide a tool that is broken behind one
    that a caller interrupted, and those want different fixes.
    """
    mapping = _outcome_map()
    assert mapping["done"] == "completed"
    assert mapping["error"] == "failed"
    assert mapping["cancelled"] == "cancelled"
    assert len(set(mapping.values())) == 3


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------


async def _seeded(tmp_path):
    from voicegateway.services.storage_service import StorageService

    storage = StorageService(str(tmp_path / "tools.db"))
    await storage._ensure_initialized()
    await storage.log_tool_calls(
        [
            ToolCall(
                session_id="s",
                call_id="c1",
                tool_name="lookup_order",
                started_at_ms=1000,
                duration_ms=2000,
                outcome="completed",
                turn_index=0,
            ),
            ToolCall(
                session_id="s",
                call_id="c2",
                tool_name="lookup_order",
                started_at_ms=5000,
                duration_ms=4000,
                outcome="failed",
                turn_index=1,
            ),
            ToolCall(
                session_id="s",
                call_id="c3",
                tool_name="send_sms",
                started_at_ms=9000,
                duration_ms=100,
                outcome="completed",
                turn_index=2,
            ),
            # In flight: no end was ever seen.
            ToolCall(
                session_id="s",
                call_id="c4",
                tool_name="send_sms",
                started_at_ms=9500,
                duration_ms=None,
                outcome=None,
                turn_index=2,
            ),
        ],
        tenant_id=None,
    )
    return storage


async def test_aggregates_group_by_tool_name(tmp_path) -> None:
    storage = await _seeded(tmp_path)
    agg = await storage.aggregate_tool_calls()
    await storage.aclose()
    assert agg["lookup_order"]["calls"] == 2
    assert agg["lookup_order"]["total_ms"] == 6000
    assert agg["lookup_order"]["failed"] == 1
    assert agg["send_sms"]["calls"] == 2


async def test_an_unfinished_call_is_counted_but_not_averaged(tmp_path) -> None:
    """Counting it as zero would pull the average toward "instant" exactly when
    a tool hung, which is the case somebody went looking for."""
    storage = await _seeded(tmp_path)
    agg = await storage.aggregate_tool_calls()
    await storage.aclose()
    assert agg["send_sms"]["calls"] == 2
    assert agg["send_sms"]["avg_ms"] == 100.0


async def test_rows_correlate_to_the_turn_they_belong_to(tmp_path) -> None:
    from voicegateway.repository import tool_calls_repository as repo

    storage = await _seeded(tmp_path)
    async with storage._conn.session() as db:
        rows = await repo.list_by_session(db, "s")
    await storage.aclose()
    assert [r.turn_index for r in rows] == [0, 1, 2, 2]


async def test_nothing_read_back_carries_a_payload(tmp_path) -> None:
    """End to end: what goes in and comes out is timing only."""
    from voicegateway.repository import tool_calls_repository as repo

    storage = await _seeded(tmp_path)
    async with storage._conn.session() as db:
        rows = await repo.list_by_session(db, "s")
    await storage.aclose()
    for row in rows:
        dumped = row.model_dump()
        assert not any(
            k in dumped for k in ("arguments", "result", "payload", "output")
        )


# --------------------------------------------------------------------------
# Remote collector, not local storage only
# --------------------------------------------------------------------------


def test_the_collector_sink_implements_tool_calls() -> None:
    """Acceptance says this must work against a remote collector.

    Asserted structurally: the sink has the method, a dedicated buffer, its own
    ingest URL, and drains on flush like turns and dead air do.
    """
    from voicegateway.services import sinks

    src = inspect.getsource(sinks)
    assert "async def log_tool_calls" in src
    assert "_tool_call_buffer" in src
    assert '"/tool-calls"' in src
    assert "await self._flush_tool_calls()" in src


def test_the_wire_shape_is_written_field_by_field() -> None:
    """So a payload column added to ToolCall later cannot start leaving the
    agent by accident. The wire shape is a decision, not a mirror of the ORM."""
    from voicegateway.services import sinks

    src = inspect.getsource(sinks.RemoteCollectorSink.log_tool_calls)
    assert "asdict(" not in src
    assert "model_dump" not in src
