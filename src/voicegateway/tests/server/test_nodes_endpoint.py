"""GET /api/nodes: the read side of the node scrape and its window correlation.

Four things these tests exist to hold:

1. **The three window statuses stay three.** ``correlated``, ``no_samples`` and
   ``scrape_failed`` are different claims -- a successful scrape overlapped the
   window, nothing was scraped in it at all, and rows exist but every scrape
   failed -- and the last hop is where they would collapse. They are asserted
   distinct in ONE response, because a surface that renders them apart in three
   separate fixtures can still merge them in the payload that carries all three.
2. **Nothing is flattened to a zero.** A window nobody scraped comes back with
   an empty ``nodes_sampled``, never a node with zeroed summaries; an unmeasured
   gauge comes back ``None`` with ``peak_stat: "not_measured"``. The whole T3/C2
   design stores NULL rather than 0 so this hop can tell an unmeasured series
   from a measured zero.
3. **The pad travels with the window.** The correlation is by TIME WINDOW, and
   "within 15 s of this call" is a weaker claim than "during this call". Both
   bounds and ``pad_ms`` are in the payload, plus a top-level ``pad_ms`` for the
   deployment that has recorded no call to carry one.
4. **The payload shape is the contract.** The panel
   (``src/dashboard/frontend/src/lib/types.ts``) is written against these field
   names, and a renamed or dropped one renders as a permanently blank card that
   still compiles. The field lists below are copied from that file and frozen.
"""

from __future__ import annotations

import dataclasses

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.repository import calls_repository as calls
from voicegateway.repository import node_correlation_repository as correlate
from voicegateway.repository import node_samples_repository as node_samples
from voicegateway.server import build_app

_URL = "/api/nodes"

# A realistic epoch millisecond, so padding never has to reach below zero.
_T0 = 1_700_000_000_000
_MINUTE = 60_000

# Copied from `NodeCorrelationResponse` in
# src/dashboard/frontend/src/lib/types.ts.
_TOP_FIELDS = ["pad_ms", "samples_stored", "calls"]

# Copied from `WindowCorrelation` / `NodeWindow` in the same file, in the order
# the dataclasses declare them.
_CORRELATION_FIELDS = ["window", "status", "nodes_sampled"]
_WINDOW_FIELDS = [
    "start_ms",
    "end_ms",
    "pad_ms",
    "requested_start_ms",
    "requested_end_ms",
]
_NODE_FIELDS = [
    "node",
    "source",
    "samples",
    "ok_samples",
    "failed_samples",
    "outcomes",
    "first_sample_at_ms",
    "last_sample_at_ms",
    "gauges",
    "counters",
    "truncated",
]
_GAUGE_FIELDS = [
    "column",
    "samples",
    "minimum",
    "maximum",
    "latest",
    "peak",
    "peak_stat",
]
_COUNTER_FIELDS = [
    "column",
    "points",
    "unknown_points",
    "peak_per_second",
    "peak_stat",
]

_BASE_CONFIG = {
    "providers": {"openai": {"api_key": "test-key"}},
    "models": {"stt": {}, "llm": {}, "tts": {}},
    "projects": {},
    "fallbacks": {"stt": [], "llm": [], "tts": []},
    "cost_tracking": {"enabled": True},
}


def _write_config(tmp_path, extra: dict | None = None) -> str:
    path = tmp_path / "voicegw.yaml"
    with open(path, "w") as fh:
        yaml.dump({**_BASE_CONFIG, **(extra or {})}, fh)
    return str(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient api key deciding whether auth applies."""
    monkeypatch.delenv("VOICEGW_API_KEY", raising=False)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "nodes-endpoint.db"))
    return Gateway(config_path=_write_config(tmp_path))


@pytest.fixture
async def client(gateway):
    transport = ASGITransport(app=build_app(gateway))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _call(
    storage,
    *,
    attempt_id: str,
    started_at_ms: int | None,
    ended_at_ms: int | None,
) -> str:
    """One call row, through the repository that owns it."""
    return await storage.upsert_call(
        origin="loadgen",
        attempt_id=attempt_id,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
    )


async def _scrape(
    storage,
    *,
    at_ms: int,
    node: str = "sfu-1",
    source: str = "livekit-server",
    outcome: str = "ok",
    **values: float | None,
) -> None:
    """One node_samples row, written the way the scrape worker writes it.

    There is no storage passthrough for the write side (the worker owns it), so
    the test reaches the repository through the same session the reader uses.
    """
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await node_samples.insert_samples(
            db,
            [
                node_samples.NodeSampleInput(
                    node=node,
                    source=source,
                    at_ms=at_ms,
                    outcome=outcome,
                    series_found=len(values) or None,
                    values=values,
                )
            ],
        )


def _by_attempt(body: dict, attempt_id: str) -> dict:
    """The one call in the payload with this attempt id."""
    matches = [c for c in body["calls"] if c["attempt_id"] == attempt_id]
    assert len(matches) == 1, f"expected exactly one {attempt_id}, got {matches}"
    return matches[0]


# --- shape ------------------------------------------------------------------


async def test_payload_fields_match_the_frontend_types_exactly(client, gateway):
    """Field for field against types.ts: no invented, renamed or dropped key."""
    await _call(
        gateway.storage,
        attempt_id="shape",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 1_000, rooms=2.0, packets_total=100.0)

    body = (await client.get(_URL)).json()

    assert list(body) == _TOP_FIELDS
    correlation = body["calls"][0]["correlation"]
    assert list(correlation) == _CORRELATION_FIELDS
    assert list(correlation["window"]) == _WINDOW_FIELDS
    node = correlation["nodes_sampled"][0]
    assert list(node) == _NODE_FIELDS
    assert list(node["gauges"]["rooms"]) == _GAUGE_FIELDS
    assert list(node["counters"]["packets_total"]) == _COUNTER_FIELDS


async def test_the_call_row_is_forwarded_whole_beside_its_correlation(client, gateway):
    """The panel labels a call the way the calls panel does, so it needs the
    same row: nothing is projected down to a subset here."""
    await _call(
        gateway.storage, attempt_id="row", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )

    call = (await client.get(_URL)).json()["calls"][0]
    stored = (await gateway.storage.list_calls(limit=1))[0]

    assert {k: v for k, v in call.items() if k != "correlation"} == stored


# --- the three statuses are three -------------------------------------------


async def test_all_three_statuses_round_trip_distinctly_in_one_response(
    client, gateway
):
    """The honesty requirement, asserted where it would be lost.

    One response carrying all three: a call whose window holds a successful
    scrape, one whose window holds only failed scrapes, and one whose window
    holds nothing at all. None of the three may render as another, and none of
    them is a zero.
    """
    # Newest first: `ok` is the most recent, `none` the oldest. The three
    # windows are 10 minutes apart, far wider than the 15 s pad, so no scrape
    # leaks between them.
    await _call(
        gateway.storage,
        attempt_id="ok",
        started_at_ms=_T0 + 20 * _MINUTE,
        ended_at_ms=_T0 + 21 * _MINUTE,
    )
    await _call(
        gateway.storage,
        attempt_id="failed",
        started_at_ms=_T0 + 10 * _MINUTE,
        ended_at_ms=_T0 + 11 * _MINUTE,
    )
    await _call(
        gateway.storage,
        attempt_id="none",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 20 * _MINUTE + 5_000, rooms=3.0)
    await _scrape(
        gateway.storage, at_ms=_T0 + 10 * _MINUTE + 5_000, outcome="unreachable"
    )

    body = (await client.get(_URL)).json()

    correlated = _by_attempt(body, "ok")["correlation"]
    scrape_failed = _by_attempt(body, "failed")["correlation"]
    no_samples = _by_attempt(body, "none")["correlation"]

    assert correlated["status"] == "correlated"
    assert scrape_failed["status"] == "scrape_failed"
    assert no_samples["status"] == "no_samples"
    # Three claims, three values, all from the repository's closed set.
    assert len({c["status"] for c in (correlated, scrape_failed, no_samples)}) == 3
    for correlation in (correlated, scrape_failed, no_samples):
        assert correlation["status"] in correlate.WINDOW_STATUSES


async def test_a_window_nobody_scraped_carries_no_node_at_all(client, gateway):
    """`no_samples` must not arrive as a node with zeroed summaries: a flat zero
    line reads as a clean bill of health."""
    await _call(
        gateway.storage,
        attempt_id="unwatched",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )

    correlation = (await client.get(_URL)).json()["calls"][0]["correlation"]

    assert correlation["status"] == "no_samples"
    assert correlation["nodes_sampled"] == []


async def test_a_window_of_failed_scrapes_is_not_a_healthy_node(client, gateway):
    """The nodes were being watched and the watching did not work. Every value
    stays null and the outcome that caused it is named."""
    await _call(
        gateway.storage,
        attempt_id="blind",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 5_000, outcome="timeout")
    await _scrape(gateway.storage, at_ms=_T0 + 20_000, outcome="timeout")

    correlation = (await client.get(_URL)).json()["calls"][0]["correlation"]
    node = correlation["nodes_sampled"][0]

    assert correlation["status"] == "scrape_failed"
    assert node["ok_samples"] == 0
    assert node["failed_samples"] == 2
    assert node["outcomes"] == {"timeout": 2}
    assert node["gauges"]["rooms"]["latest"] is None
    assert node["gauges"]["rooms"]["peak"] is None
    assert node["gauges"]["rooms"]["peak_stat"] == "not_measured"


async def test_an_unmeasured_series_is_null_and_says_so_never_zero(client, gateway):
    """A source only carries its own series. node-exporter has no `rooms`, and
    that column must read as not measured rather than as an empty SFU."""
    await _call(
        gateway.storage,
        attempt_id="host",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(
        gateway.storage,
        at_ms=_T0 + 5_000,
        source="node-exporter",
        load1=0.75,
        filefd_allocated=1_024.0,
    )

    node = (await client.get(_URL)).json()["calls"][0]["correlation"]["nodes_sampled"][
        0
    ]

    assert node["gauges"]["load1"]["latest"] == pytest.approx(0.75)
    assert node["gauges"]["rooms"]["samples"] == 0
    assert node["gauges"]["rooms"]["latest"] is None
    assert node["gauges"]["rooms"]["peak"] is None
    assert node["gauges"]["rooms"]["peak"] != 0
    assert node["gauges"]["rooms"]["peak_stat"] == "not_measured"


async def test_a_call_with_no_closed_window_is_not_no_samples(client, gateway):
    """An in-flight call was never looked for, which is a different fact from a
    window that was looked at and held nothing."""
    await _call(
        gateway.storage, attempt_id="in-flight", started_at_ms=_T0, ended_at_ms=None
    )

    call = (await client.get(_URL)).json()["calls"][0]

    assert call["ended_at_ms"] is None
    assert call["correlation"] is None


# --- the pad is published, not assumed --------------------------------------


async def test_every_window_carries_the_pad_and_both_bounds(client, gateway):
    """The correlation is by time window, so the reader must be able to see how
    much was added to it and never be told the padded region is the call."""
    await _call(
        gateway.storage, attempt_id="pad", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )

    body = (await client.get(_URL)).json()
    window = body["calls"][0]["correlation"]["window"]

    assert body["pad_ms"] == correlate.DEFAULT_WINDOW_PAD_MS
    assert window["pad_ms"] == correlate.DEFAULT_WINDOW_PAD_MS
    assert (window["requested_start_ms"], window["requested_end_ms"]) == (
        _T0,
        _T0 + _MINUTE,
    )
    assert window["start_ms"] == _T0 - correlate.DEFAULT_WINDOW_PAD_MS
    assert window["end_ms"] == _T0 + _MINUTE + correlate.DEFAULT_WINDOW_PAD_MS


async def test_the_pad_is_readable_even_with_no_call_to_carry_one(client):
    """A fresh deployment has no window, and the pad it would be correlated
    against is still the parameter the reader is entitled to see."""
    body = (await client.get(_URL)).json()

    assert body["calls"] == []
    assert body["pad_ms"] == correlate.DEFAULT_WINDOW_PAD_MS


async def test_the_pad_is_a_parameter_and_the_payload_reports_the_one_used(
    client, gateway
):
    await _call(
        gateway.storage,
        attempt_id="unpadded",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )

    body = (await client.get(f"{_URL}?pad_ms=0")).json()
    window = body["calls"][0]["correlation"]["window"]

    assert body["pad_ms"] == 0
    assert window["pad_ms"] == 0
    assert (window["start_ms"], window["end_ms"]) == (_T0, _T0 + _MINUTE)


async def test_the_pad_actually_widens_which_samples_correlate(client, gateway):
    """A scrape 5 s after the call ended is inside the padded window and outside
    the requested one, which is exactly why the pad has to be visible."""
    await _call(
        gateway.storage, attempt_id="edge", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )
    await _scrape(gateway.storage, at_ms=_T0 + _MINUTE + 5_000, rooms=1.0)

    padded = (await client.get(_URL)).json()["calls"][0]["correlation"]
    exact = (await client.get(f"{_URL}?pad_ms=0")).json()["calls"][0]["correlation"]

    assert padded["status"] == "correlated"
    assert exact["status"] == "no_samples"


# --- the endpoint computes nothing ------------------------------------------


async def test_the_served_correlation_is_the_one_the_repository_computed(
    client, gateway
):
    """Pure passthrough: no rounding, no recomputation, no second opinion."""
    call_id = await _call(
        gateway.storage,
        attempt_id="passthrough",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 5_000, rooms=4.0, packets_total=10.0)
    await _scrape(gateway.storage, at_ms=_T0 + 20_000, rooms=6.0, packets_total=40.0)

    body = (await client.get(_URL)).json()
    async with gateway.storage._conn.session() as db:
        row = await calls.get_call(db, call_id)
        assert row is not None
        computed = await correlate.correlate_call_window(db, call=row)

    assert computed is not None
    assert body["calls"][0]["correlation"] == dataclasses.asdict(computed)


async def test_a_peak_from_too_few_samples_is_labelled_max_of_n(client, gateway):
    """Decision 3, surfaced rather than re-decided: below 10 measured points the
    peak is a maximum and says so, so nobody reads it as a p95."""
    await _call(
        gateway.storage, attempt_id="few", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )
    for index in range(3):
        await _scrape(gateway.storage, at_ms=_T0 + 5_000 * (index + 1), rooms=index + 1)

    gauge = (await client.get(_URL)).json()["calls"][0]["correlation"]["nodes_sampled"][
        0
    ]["gauges"]["rooms"]

    assert gauge["samples"] == 3
    assert gauge["samples"] < correlate.MIN_PERCENTILE_SAMPLES
    assert gauge["peak_stat"] == "max_of_n"
    assert gauge["peak_stat"] in correlate.PEAK_STATS
    assert gauge["peak"] == pytest.approx(3.0)


async def test_a_counter_reset_inside_the_window_stays_unknown_not_a_spike(
    client, gateway
):
    """The reset rule lives in `counter_rates` and is reported, not repaired: a
    restart lowers confidence instead of publishing a rate."""
    await _call(
        gateway.storage,
        attempt_id="restart",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 5_000, packets_total=1_000.0)
    await _scrape(gateway.storage, at_ms=_T0 + 20_000, packets_total=5.0)

    counter = (await client.get(_URL)).json()["calls"][0]["correlation"][
        "nodes_sampled"
    ][0]["counters"]["packets_total"]

    assert counter["points"] == 2
    assert counter["unknown_points"] == 2
    assert counter["peak_per_second"] is None
    assert counter["peak_stat"] == "not_measured"


async def test_two_concurrent_calls_correlate_to_the_same_node(client, gateway):
    """Overlap is not attribution. Both calls list the node, because both were
    open while it was scraped, and neither claim means it served them."""
    await _call(
        gateway.storage, attempt_id="a", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )
    await _call(
        gateway.storage,
        attempt_id="b",
        started_at_ms=_T0 + 1_000,
        ended_at_ms=_T0 + _MINUTE,
    )
    await _scrape(gateway.storage, at_ms=_T0 + 30_000, rooms=2.0)

    body = (await client.get(_URL)).json()

    for attempt_id in ("a", "b"):
        correlation = _by_attempt(body, attempt_id)["correlation"]
        assert correlation["status"] == "correlated"
        assert [n["node"] for n in correlation["nodes_sampled"]] == ["sfu-1"]


# --- the empty table is a state, not an error -------------------------------


async def test_an_empty_node_samples_table_is_not_an_error(client, gateway):
    """The state every operator starts in, and the one before the scrape worker
    is wired or a target is configured."""
    await _call(
        gateway.storage,
        attempt_id="fresh",
        started_at_ms=_T0,
        ended_at_ms=_T0 + _MINUTE,
    )

    resp = await client.get(_URL)
    body = resp.json()

    assert resp.status_code == 200
    assert body["samples_stored"] == 0
    assert body["calls"][0]["correlation"]["status"] == "no_samples"
    assert body["calls"][0]["correlation"]["nodes_sampled"] == []


async def test_the_stored_row_count_separates_unscraped_from_unsampled(client, gateway):
    """`no_samples` on one window does not say whether anything is scraped at
    all, so the row count is published beside it."""
    await _call(
        gateway.storage, attempt_id="old", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )
    await _scrape(gateway.storage, at_ms=_T0 + 60 * _MINUTE, rooms=1.0)

    body = (await client.get(_URL)).json()

    assert body["samples_stored"] == 1
    assert body["calls"][0]["correlation"]["status"] == "no_samples"


# --- bounds -----------------------------------------------------------------


async def test_the_call_limit_is_capped(client):
    assert (await client.get(f"{_URL}?limit=999")).status_code == 422


async def test_the_pad_may_not_be_negative_or_unbounded(client):
    assert (await client.get(f"{_URL}?pad_ms=-1")).status_code == 422
    assert (await client.get(f"{_URL}?pad_ms=99999999")).status_code == 422


# --- auth -------------------------------------------------------------------


async def test_auth_is_required_when_api_keys_are_configured(tmp_path, monkeypatch):
    """Router-level require_principal: a handler cannot forget to opt in."""
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "nodes-auth.db"))
    config = _write_config(
        tmp_path, {"auth": {"api_keys": [{"name": "ui", "token": "sk-configured"}]}}
    )
    gw = Gateway(config_path=config)
    transport = ASGITransport(app=build_app(gw))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get(_URL)).status_code == 401
        assert (
            await c.get(_URL, headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        ok = await c.get(_URL, headers={"Authorization": "Bearer sk-configured"})

    assert ok.status_code == 200
    assert ok.json()["calls"] == []


async def test_the_operator_default_with_no_keys_still_reads_it(client, gateway):
    """No credential = the self-hosted operator, unchanged."""
    await _call(
        gateway.storage, attempt_id="op", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )

    resp = await client.get(_URL)

    assert resp.status_code == 200
    assert len(resp.json()["calls"]) == 1


async def test_a_tenant_key_is_refused_rather_than_shown_the_whole_fleet(
    client, gateway
):
    """A node is infrastructure and serves every tenant at once, so there is no
    tenant-scoped version of this payload to hand back."""
    await _call(
        gateway.storage, attempt_id="t", started_at_ms=_T0, ended_at_ms=_T0 + _MINUTE
    )
    async with gateway.storage._conn.session() as db:
        created = await api_keys.create_api_key(db, name="acme-ui", tenant_id="acme")

    resp = await client.get(
        _URL, headers={"Authorization": f"Bearer {created.plaintext}"}
    )

    assert resp.status_code == 403


# --- storage disabled -------------------------------------------------------


async def test_storage_disabled_returns_503_not_an_empty_correlation(
    tmp_path, monkeypatch
):
    """ "Nothing was scraped" and "this deployment records nothing" are different
    facts."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    config = _write_config(tmp_path, {"cost_tracking": {"enabled": False}})
    gw = Gateway(config_path=config)
    assert gw.storage is None
    transport = ASGITransport(app=build_app(gw, enable_dashboard=False))

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(_URL)

    assert resp.status_code == 503
