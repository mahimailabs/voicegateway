"""Contract tests for voicegateway.middleware.state_snapshotter_middleware (T03 of v0.3.0)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from voicegateway.middleware.state_snapshotter_middleware import StateSnapshotter


async def test_on_message_added_emits_snapshot() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=0)
    fired = await snapper.on_message_added(
        system_prompt="be helpful",
        message_history=[{"role": "user", "content": "hi"}],
        session_id="s1",
    )
    assert fired is True
    assert len(captured) == 1
    assert captured[0]["system_prompt"] == "be helpful"
    assert len(captured[0]["message_history"]) == 1


async def test_rate_cap_drops_within_window() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=1.0)
    fired1 = await snapper.on_message_added(
        message_history=[{"role": "user", "content": "a"}],
        session_id="s1",
    )
    # Immediately fire again; within the 1s window so should drop.
    fired2 = await snapper.on_message_added(
        message_history=[{"role": "assistant", "content": "b"}],
        session_id="s1",
    )

    assert fired1 is True
    assert fired2 is False
    assert len(captured) == 1


async def test_rate_cap_releases_after_window() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=0.05)
    await snapper.on_message_added(session_id="s1")
    await asyncio.sleep(0.08)
    fired2 = await snapper.on_message_added(session_id="s1")
    assert fired2 is True
    assert len(captured) == 2


async def test_tool_resolve_bypasses_rate_cap() -> None:
    """Tool-resolve is structurally important; rate cap must NOT drop it."""
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(
        on_snapshot=on_snap,
        min_interval_seconds=10.0,  # large window
    )
    fired_msg = await snapper.on_message_added(session_id="s1")
    fired_tool = await snapper.on_tool_resolved(
        tool_name="search",
        tool_args={"q": "hi"},
        result={"hits": [1, 2, 3]},
        session_id="s1",
    )

    assert fired_msg is True
    assert fired_tool is True  # bypassed
    assert len(captured) == 2


async def test_on_tool_invoked_records_in_flight() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=0)
    await snapper.on_tool_invoked(
        tool_name="lookup",
        tool_args={"id": 42},
        session_id="s1",
    )

    assert captured[0]["tool_call_in_flight"] == {
        "name": "lookup",
        "args": {"id": 42},
        "result": None,
    }


async def test_no_session_id_drops_snapshot() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=0)
    fired = await snapper.on_message_added(session_id=None)
    assert fired is False
    assert captured == []


async def test_callback_error_is_swallowed() -> None:
    """A failing callback returns False but does not propagate."""

    async def failing(snap: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    snapper = StateSnapshotter(on_snapshot=failing, min_interval_seconds=0)
    fired = await snapper.on_message_added(session_id="s1")
    assert fired is False


async def test_invalid_interval_rejected() -> None:
    with pytest.raises(ValueError):
        StateSnapshotter(min_interval_seconds=-1.0)


async def test_reset_session_clears_rate_cap() -> None:
    captured: list[dict[str, Any]] = []

    async def on_snap(snap: dict[str, Any]) -> None:
        captured.append(snap)

    snapper = StateSnapshotter(on_snapshot=on_snap, min_interval_seconds=10.0)
    await snapper.on_message_added(session_id="s1")
    snapper.reset_session("s1")
    # Within the original 10s window but the reset cleared the timestamp.
    fired = await snapper.on_message_added(session_id="s1")
    assert fired is True
    assert len(captured) == 2
