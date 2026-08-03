"""The diagnostics gate series on GET /v1/metrics, and what it refuses to say.

The point of a metrics surface is that every number on it was measured. In
Prometheus an exported ``0`` is an observation: it is graphed, alerted on and
believed. So the assertions here are as much about the lines that must be
ABSENT as the ones that must be present -- a deployment that has never run
diagnostics must publish no gate series at all, rather than a green-looking zero.

The other half is the ladder. ``livekit_diag.gates`` ranks PASS < WARN <
UNKNOWN < FAIL, where UNKNOWN means "could not evaluate" and exits non-zero. If
this exposition collapsed UNKNOWN into PASS it would undo that gate in the one
place an operator actually watches.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app


@pytest.fixture
def gateway(temp_config, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "metrics-diag.db"))
    return Gateway(config_path=temp_config)


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_run(
    gateway: Gateway,
    *,
    run_id: str,
    created_at: str,
    status: str = "done",
    gates: list[dict[str, Any]] | None = None,
    verdict: str | None = None,
    ended_at: str | None = None,
) -> None:
    """Store one diagnostics run exactly as the dashboard endpoint stores it."""
    results = (
        None if gates is None else {"checks": {}, "gates": gates, "verdict": verdict}
    )
    await gateway.storage.upsert_diagnostics_run(
        run_id=run_id,
        checks=["agents", "latency"],
        config={"target_ms": 1500},
        status=status,
        results=results,
        verdict=verdict,
        created_at=created_at,
        started_at=created_at,
        ended_at=ended_at,
    )


def _diag_lines(text: str) -> list[str]:
    """Only the sample lines (not HELP/TYPE) of the diagnostics series."""
    return [ln for ln in text.splitlines() if ln.startswith("voicegw_diag")]


async def test_no_run_means_no_gate_series_at_all(client) -> None:
    """Never evaluated is an ABSENT series, never a zero.

    A ``voicegw_diag_gate_status ... 0`` here would be read as "a gate ran and
    reported the healthy end of the ladder", which is precisely what did not
    happen.
    """
    resp = await client.get("/v1/metrics")
    assert resp.status_code == 200
    assert _diag_lines(resp.text) == []
    assert "voicegw_diag" not in resp.text
    # The pre-existing exposition is untouched.
    assert "voicegw_uptime_seconds" in resp.text


async def test_each_status_gets_its_own_series_and_unknown_is_not_pass(
    client, gateway
) -> None:
    """All four rungs of the ladder survive the round trip, distinctly."""
    ended = "2026-07-30T12:00:00+00:00"
    await _seed_run(
        gateway,
        run_id="r1",
        created_at="2026-07-30T11:59:00+00:00",
        ended_at=ended,
        verdict="FAIL",
        gates=[
            {"gate": "agents_listing", "status": "PASS", "detail": "1 agent"},
            {"gate": "agent_reply_latency", "status": "WARN", "subject": "a1"},
            {"gate": "sfu_connection_quality", "status": "UNKNOWN"},
            {"gate": "sfu_capacity", "status": "FAIL"},
        ],
    )
    text = (await client.get("/v1/metrics")).text

    assert 'voicegw_diag_gate_status{gate="agents_listing",status="PASS"} 1' in text
    assert (
        'voicegw_diag_gate_status{gate="agent_reply_latency",status="WARN"} 1' in text
    )
    assert (
        'voicegw_diag_gate_status{gate="sfu_connection_quality",status="UNKNOWN"} 1'
        in text
    )
    assert 'voicegw_diag_gate_status{gate="sfu_capacity",status="FAIL"} 1' in text
    # UNKNOWN never renders as the healthy rung: it carries its own label value,
    # and the gate that could not evaluate does not appear as a PASS anywhere.
    assert 'gate="sfu_connection_quality",status="PASS"' not in text

    assert 'voicegw_diag_run_verdict{verdict="FAIL"} 1' in text
    assert 'voicegw_diag_run_verdict{verdict="PASS"}' not in text

    expected = datetime.fromisoformat(ended).timestamp()
    assert f"voicegw_diag_run_timestamp_seconds {expected:.3f}" in text

    assert "# TYPE voicegw_diag_gate_status gauge" in text
    assert "# TYPE voicegw_diag_run_verdict gauge" in text
    assert "# TYPE voicegw_diag_run_timestamp_seconds gauge" in text


async def test_repeated_gate_ids_are_counted_not_duplicated(client, gateway) -> None:
    """One latency gate per probed agent collapses into a count, not N series.

    Two samples sharing a label set is a malformed exposition (Prometheus fails
    the whole scrape), and the fix is not a per-agent label: that grows with the
    number of agents in rooms.
    """
    await _seed_run(
        gateway,
        run_id="r1",
        created_at="2026-07-30T11:59:00+00:00",
        ended_at="2026-07-30T12:00:00+00:00",
        verdict="WARN",
        gates=[
            {"gate": "agent_reply_latency", "status": "PASS", "subject": "agent-one"},
            {"gate": "agent_reply_latency", "status": "PASS", "subject": "agent-two"},
            {"gate": "agent_reply_latency", "status": "WARN", "subject": "agent-three"},
        ],
    )
    text = (await client.get("/v1/metrics")).text

    assert (
        'voicegw_diag_gate_status{gate="agent_reply_latency",status="PASS"} 2' in text
    )
    assert (
        'voicegw_diag_gate_status{gate="agent_reply_latency",status="WARN"} 1' in text
    )

    lines = _diag_lines(text)
    label_sets = [ln.rsplit(" ", 1)[0] for ln in lines]
    assert len(label_sets) == len(set(label_sets)), lines

    # Aggregate only: no per-agent label, no probed agent's name on the wire.
    assert "subject=" not in text
    assert "agent-three" not in text


async def test_a_run_in_flight_does_not_blank_the_last_gated_run(
    client, gateway
) -> None:
    """A queued run has no gates yet; the newest run that gated something wins."""
    await _seed_run(
        gateway,
        run_id="r1",
        created_at="2026-07-30T11:00:00+00:00",
        ended_at="2026-07-30T11:01:00+00:00",
        verdict="UNKNOWN",
        gates=[{"gate": "agent_reply_latency", "status": "UNKNOWN"}],
    )
    await _seed_run(
        gateway,
        run_id="r2",
        created_at="2026-07-30T12:00:00+00:00",
        status="queued",
        gates=None,
    )
    text = (await client.get("/v1/metrics")).text

    assert (
        'voicegw_diag_gate_status{gate="agent_reply_latency",status="UNKNOWN"} 1'
        in text
    )
    assert 'voicegw_diag_run_verdict{verdict="UNKNOWN"} 1' in text
    older = datetime.fromisoformat("2026-07-30T11:01:00+00:00").timestamp()
    # The timestamp belongs to the run the gates came from, so the staleness an
    # operator reads is the staleness of the verdict.
    assert f"voicegw_diag_run_timestamp_seconds {older:.3f}" in text


async def test_a_status_this_build_cannot_encode_is_omitted(client, gateway) -> None:
    """An unrecognised status is left out, not rounded to either end."""
    await _seed_run(
        gateway,
        run_id="r1",
        created_at="2026-07-30T11:59:00+00:00",
        ended_at="2026-07-30T12:00:00+00:00",
        verdict="DEGRADED",
        gates=[{"gate": "sfu_capacity", "status": "DEGRADED"}],
    )
    text = (await client.get("/v1/metrics")).text

    assert _diag_lines(text) == []
    assert "DEGRADED" not in text


async def test_an_unzoned_timestamp_is_dropped_but_the_gates_stay(
    client, gateway
) -> None:
    """A timestamp with no offset could be off by hours, so it is not published."""
    await _seed_run(
        gateway,
        run_id="r1",
        created_at="2026-07-30T11:59:00",
        ended_at="2026-07-30T12:00:00",
        verdict="PASS",
        gates=[{"gate": "agents_listing", "status": "PASS"}],
    )
    text = (await client.get("/v1/metrics")).text

    assert 'voicegw_diag_gate_status{gate="agents_listing",status="PASS"} 1' in text
    assert "voicegw_diag_run_timestamp_seconds" not in text


async def test_an_unreadable_table_omits_the_series_without_failing_the_scrape(
    client, gateway
) -> None:
    """A diagnostics read that raises costs the gate series, not the whole scrape.

    An older database whose migration has not been applied must not take
    ``voicegw_uptime_seconds`` and the cost counters down with it.
    """

    async def _boom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("no such table: diagnostics_runs")

    gateway.storage.list_diagnostics_runs = _boom  # type: ignore[method-assign]

    resp = await client.get("/v1/metrics")
    assert resp.status_code == 200
    assert _diag_lines(resp.text) == []
    assert "voicegw_uptime_seconds" in resp.text
    assert "voicegw_cost_usd_total" in resp.text
