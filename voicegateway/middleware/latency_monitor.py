"""TTFB and total latency tracking."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LatencyMeasurement:
    """Result of a latency measurement."""

    ttfb_ms: float
    total_ms: float
    model_id: str


class LatencyMonitor:
    """Tracks TTFB + total latency for requests."""

    def __init__(self, ttfb_warning_ms: float = 500.0):
        self._ttfb_warning_ms = ttfb_warning_ms

    def start(self) -> _LatencyTimer:
        """Start timing a request."""
        return _LatencyTimer(self._ttfb_warning_ms)


class _LatencyTimer:
    """Timer for measuring request latency."""

    def __init__(self, ttfb_warning_ms: float):
        self._start = time.perf_counter()
        self._first_byte: float | None = None
        self._ttfb_warning_ms = ttfb_warning_ms

    def mark_first_byte(self) -> None:
        """Mark when the first byte/token was received."""
        if self._first_byte is None:
            self._first_byte = time.perf_counter()
            ttfb = (self._first_byte - self._start) * 1000
            if ttfb > self._ttfb_warning_ms:
                logger.warning(
                    f"High TTFB: {ttfb:.1f}ms (threshold: {self._ttfb_warning_ms}ms)"
                )

    def finish(self, model_id: str = "") -> LatencyMeasurement:
        """Finish timing and return the measurement."""
        end = time.perf_counter()
        ttfb = ((self._first_byte or end) - self._start) * 1000
        total = (end - self._start) * 1000
        return LatencyMeasurement(ttfb_ms=ttfb, total_ms=total, model_id=model_id)
