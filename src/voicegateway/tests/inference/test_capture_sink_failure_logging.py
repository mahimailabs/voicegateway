"""A failing sink must not stamp a stack trace per write into the host's console.

The attach sink writes once per metric event. When its storage cannot be
migrated, every one of those writes raised, and every raise logged a full
alembic traceback: roughly two hundred lines every few seconds for the life of
the LiveKit job. Log the first failure of each kind in full, then quietly.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from voicegateway.inference.session.capture import MetricCapture


class _BrokenSink:
    """Sink whose every write fails the same way."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def log_request(self, record: object) -> None:
        self.calls += 1
        raise self._exc

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _capture(sink: object) -> MetricCapture:
    return MetricCapture(
        cost_tracker=None,  # type: ignore[arg-type]  # unused on the failure path
        sink=sink,  # type: ignore[arg-type]
        project="default",
        agent_id=None,
        session_id=None,
    )


async def _drive(capture: MetricCapture, sink: _BrokenSink, times: int) -> None:
    for _ in range(times):
        capture._schedule(sink.log_request(object()))
    await capture.drain()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_repeated_identical_failures_log_one_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = _BrokenSink(RuntimeError("migration is impossible"))
    capture = _capture(sink)

    with caplog.at_level(
        logging.DEBUG, logger="voicegateway.inference.session.capture"
    ):
        await _drive(capture, sink, times=6)

    assert sink.calls == 6, "every write should still have been attempted"

    with_trace = [r for r in caplog.records if r.exc_info is not None]
    assert len(with_trace) == 1, (
        f"expected exactly one traceback, got {len(with_trace)}"
    )
    assert with_trace[0].levelno == logging.WARNING

    # The rest are still visible, just not at WARNING and not with a trace.
    suppressed = [r for r in caplog.records if r.exc_info is None]
    assert len(suppressed) == 5
    assert all(r.levelno == logging.DEBUG for r in suppressed)


@pytest.mark.asyncio
async def test_a_new_failure_kind_still_gets_its_own_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Suppression is per exception type, so a second, different fault is not hidden."""
    first = _BrokenSink(RuntimeError("boom"))
    second = _BrokenSink(ValueError("a different fault"))
    capture = _capture(first)

    with caplog.at_level(
        logging.DEBUG, logger="voicegateway.inference.session.capture"
    ):
        await _drive(capture, first, times=3)
        await _drive(capture, second, times=3)

    traced = [r for r in caplog.records if r.exc_info is not None]
    assert len(traced) == 2, "each distinct failure type deserves one traceback"
    assert {r.exc_info[0] for r in traced} == {RuntimeError, ValueError}
