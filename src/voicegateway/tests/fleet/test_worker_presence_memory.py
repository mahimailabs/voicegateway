"""presence() carries the worker's memory sample."""

from __future__ import annotations

from voicegateway.fleet import worker as worker_mod


def test_presence_includes_memory_fields() -> None:
    w = worker_mod._Worker(
        agent_id="a1",
        agent_name="bot",
        version="0.0.0",
        project="default",
        tenant_id=None,
        region=None,
        host="h",
        started_at=1000.0,
    )
    p = w.presence()
    assert "memory_rss_bytes" in p
    assert "memory_total_bytes" in p
    # On a real host these sample to positive ints; the contract allows None.
    assert p["memory_rss_bytes"] is None or p["memory_rss_bytes"] > 0
    assert p["memory_total_bytes"] is None or p["memory_total_bytes"] > 0
