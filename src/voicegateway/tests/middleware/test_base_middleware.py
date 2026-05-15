"""Smoke tests for the Protocol contracts in middleware/base_middleware.py.

These tests enforce that every middleware class declared to satisfy a
base-layer Protocol stays in sync with the contract surface. Add a new
row whenever a new class joins one of the families.
"""

from __future__ import annotations

import pytest

from voicegateway.middleware.base_middleware import (
    AsyncWorker,
    InstrumentationMixin,
    MiddlewareError,
    PerSessionWatcher,
    SessionScopedComponent,
)
from voicegateway.middleware.budget_enforcer_middleware import (
    BudgetExceededError,
    BudgetThrottleSignal,
)
from voicegateway.middleware.dead_air_detector_middleware import DeadAirDetector
from voicegateway.middleware.instrumented_provider_middleware import (
    InstrumentedLLM,
    InstrumentedSTT,
    InstrumentedTTS,
)
from voicegateway.middleware.latency_observations_worker_middleware import (
    LatencyObservationsWorker,
)
from voicegateway.middleware.rate_limiter_middleware import RateLimitExceeded
from voicegateway.middleware.replay_capture_middleware import ReplayCapture
from voicegateway.middleware.state_snapshotter_middleware import StateSnapshotter
from voicegateway.middleware.turn_tracker_middleware import TurnTracker
from voicegateway.services.routing_service import BudgetExceeded


@pytest.mark.parametrize(
    "cls",
    [TurnTracker, ReplayCapture, StateSnapshotter],
)
def test_session_scoped_components_conform(cls: type) -> None:
    assert issubclass(cls, SessionScopedComponent)


def test_per_session_watcher_conforms() -> None:
    assert issubclass(DeadAirDetector, PerSessionWatcher)


def test_async_worker_conforms() -> None:
    assert issubclass(LatencyObservationsWorker, AsyncWorker)


@pytest.mark.parametrize(
    "cls",
    [InstrumentedSTT, InstrumentedLLM, InstrumentedTTS],
)
def test_instrumented_providers_use_mixin(cls: type) -> None:
    assert issubclass(cls, InstrumentationMixin)


@pytest.mark.parametrize(
    "cls",
    [BudgetExceededError, BudgetThrottleSignal, RateLimitExceeded, BudgetExceeded],
)
def test_middleware_errors_share_base(cls: type) -> None:
    assert issubclass(cls, MiddlewareError)


def test_state_snapshotter_close_session_drops_session() -> None:
    snapshotter = StateSnapshotter()
    snapshotter._last_snapshot_ms["sess-a"] = 1
    snapshotter._last_snapshot_ms["sess-b"] = 2

    import asyncio

    asyncio.run(snapshotter.close_session("sess-a"))

    assert "sess-a" not in snapshotter._last_snapshot_ms
    assert snapshotter.active_sessions() == ["sess-b"]
