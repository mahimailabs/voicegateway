"""presence() carries the worker's memory + CPU sample."""

from __future__ import annotations

from voicegateway.fleet import worker as worker_mod


def _worker() -> worker_mod._Worker:
    return worker_mod._Worker(
        agent_id="a1",
        agent_name="bot",
        version="0.0.0",
        project="default",
        tenant_id=None,
        region=None,
        host="h",
        started_at=1000.0,
    )


def test_presence_includes_memory_fields() -> None:
    p = _worker().presence()
    assert "memory_rss_bytes" in p
    assert "memory_total_bytes" in p
    # On a real host these sample to positive ints; the contract allows None.
    assert p["memory_rss_bytes"] is None or p["memory_rss_bytes"] > 0
    assert p["memory_total_bytes"] is None or p["memory_total_bytes"] > 0


def test_presence_includes_cpu_pct() -> None:
    p = _worker().presence()
    assert "cpu_pct" in p
    # None on a failed sample, else a machine-capacity share in [0, 100].
    assert p["cpu_pct"] is None or (0.0 <= p["cpu_pct"] <= 100.0)
