"""NodeSamplesWorker: canned exposition in, ``node_samples`` rows out.

Every scrape here goes through an ``httpx.MockTransport``. Nothing in this file
opens a socket, and no AWS or LiveKit endpoint is contacted.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from voicegateway.middleware.node_samples_worker_middleware import (
    OUTCOME_HTTP_ERROR,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    OUTCOME_TOO_LARGE,
    OUTCOME_UNPARSEABLE,
    OUTCOME_UNREACHABLE,
    SERIES,
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
    TARGETS_ENV_VAR,
    NodeSamplesWorker,
    ScrapeTarget,
    targets_from_env,
)
from voicegateway.repository import node_samples_repository as repo
from voicegateway.services.storage_service import StorageService

# ---------------------------------------------------------------------------
# Canned exposition bodies (see the worker's module docstring for provenance)
# ---------------------------------------------------------------------------

LIVEKIT_SERVER_EXPOSITION = """\
# HELP livekit_room_total rooms
# TYPE livekit_room_total gauge
livekit_room_total{node_id="ND_abc",node_type="SERVER"} 3
livekit_participant_total{node_id="ND_abc",node_type="SERVER"} 11
# TYPE livekit_packet_total counter
livekit_packet_total{direction="incoming",transmission="rtp",country="ca"} 4000
livekit_packet_total{direction="outgoing",transmission="rtp",country="ca"} 6000
livekit_packet_bytes{direction="incoming",transmission="rtp"} 2500000000
livekit_packet_bytes{direction="outgoing",transmission="rtp"} 1500000000
livekit_nack_total{direction="incoming"} 12
"""

LIVEKIT_SIP_EXPOSITION = """\
livekit_sip_calls_active 7
livekit_sip_invite_requests_raw 900
livekit_sip_invite_requests 880
livekit_sip_invite_accepted 870
livekit_sip_calls_terminated{status="ok"} 800
livekit_sip_calls_terminated{status="failed"} 60
"""

NODE_EXPORTER_EXPOSITION = """\
node_filefd_allocated 1216
node_filefd_maximum 9223372036854775807
node_load1 1.75
node_cpu_seconds_total{cpu="0",mode="idle"} 1000
node_cpu_seconds_total{cpu="0",mode="user"} 200
node_cpu_seconds_total{cpu="1",mode="idle"} 1100
node_cpu_seconds_total{cpu="1",mode="system"} 50
node_memory_MemTotal_bytes 16777216000
node_memory_MemAvailable_bytes 8388608000
"""

_BODIES = {
    "http://sfu-1:6789/metrics": LIVEKIT_SERVER_EXPOSITION,
    "http://sip-1:8080/metrics": LIVEKIT_SIP_EXPOSITION,
    "http://sfu-1:9100/metrics": NODE_EXPORTER_EXPOSITION,
}


@pytest.fixture
async def storage(tmp_path):
    service = StorageService(db_path=str(tmp_path / "nodes.db"))
    try:
        yield service
    finally:
        # Dispose inside the test's own event loop. aiosqlite runs its
        # connection on a thread that calls back into the loop that created it,
        # so an engine left to the garbage collector raises "Event loop is
        # closed" from that thread during whichever later test happens to be
        # running -- the thread-exception flake this suite already knows.
        await service.aclose()


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _canned(request: httpx.Request) -> httpx.Response:
    body = _BODIES.get(str(request.url))
    if body is None:
        return httpx.Response(404, text="not found")
    return httpx.Response(200, text=body)


def _provider(*targets: ScrapeTarget):
    async def provider():
        return list(targets)

    return provider


async def _rows(storage, node: str, source: str):
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        return await repo.list_samples(db, node=node, source=source)


SERVER_TARGET = ScrapeTarget(
    node="sfu-1", url="http://sfu-1:6789/metrics", source=SOURCE_LIVEKIT_SERVER
)
SIP_TARGET = ScrapeTarget(
    node="sip-1", url="http://sip-1:8080/metrics", source=SOURCE_LIVEKIT_SIP
)
HOST_TARGET = ScrapeTarget(
    node="sfu-1", url="http://sfu-1:9100/metrics", source=SOURCE_NODE_EXPORTER
)


# ---------------------------------------------------------------------------
# Happy path, per source
# ---------------------------------------------------------------------------


async def test_livekit_server_exposition_lands_in_columns(storage) -> None:
    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SERVER_TARGET), transport=_transport(_canned)
    )
    assert await worker.tick_now() == 1

    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.outcome == OUTCOME_OK
    assert row.rooms == 3
    assert row.participants == 11
    # Summed across direction/transmission/country: this table stores the node
    # total, not a per-label split.
    assert row.packets_total == 10_000
    assert row.packet_bytes_total == 4_000_000_000  # > 2 GiB, the INT4 trap
    # 4, not 5: livekit_nack_total is still in the fixture above but is no
    # longer mapped, because it does not exist on livekit-server 1.10.1.
    # An unmapped metric is ignored and uncounted, which is what this now
    # also demonstrates.
    assert row.series_found == 4
    # Nothing from another exporter leaked in.
    assert row.filefd_allocated is None
    assert row.sip_calls_active is None


async def test_livekit_sip_exposition_lands_in_columns(storage) -> None:
    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SIP_TARGET), transport=_transport(_canned)
    )
    await worker.tick_now()

    row = (await _rows(storage, "sip-1", SOURCE_LIVEKIT_SIP))[0]
    assert row.sip_calls_active == 7
    assert row.sip_invite_requests_raw_total == 900
    assert row.sip_invite_requests_total == 880
    assert row.sip_invite_accepted_total == 870
    assert row.sip_calls_terminated_total == 860  # summed across `status`
    assert row.series_found == 5


async def test_node_exporter_exposition_lands_in_columns(storage) -> None:
    worker = NodeSamplesWorker(
        storage, target_provider=_provider(HOST_TARGET), transport=_transport(_canned)
    )
    await worker.tick_now()

    row = (await _rows(storage, "sfu-1", SOURCE_NODE_EXPORTER))[0]
    # The M4 headline pair.
    assert row.filefd_allocated == 1216
    # fs.file-max near 2**63: does not survive a float64 round trip, so it is
    # dropped rather than stored one past the 64-bit ceiling.
    assert row.filefd_maximum is None
    assert row.load1 == pytest.approx(1.75)
    assert row.cpu_seconds_total == pytest.approx(2350.0)
    assert row.cpu_idle_seconds_total == pytest.approx(2100.0)
    assert row.memory_total_bytes == 16_777_216_000
    assert row.memory_available_bytes == 8_388_608_000


async def test_one_node_scraped_by_two_exporters_shares_a_timestamp(storage) -> None:
    """Layer 7 correlates by (node, time). Both rows must land on one instant."""
    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET, HOST_TARGET),
        transport=_transport(_canned),
    )
    assert await worker.tick_now() == 2

    server = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    host = (await _rows(storage, "sfu-1", SOURCE_NODE_EXPORTER))[0]
    assert server.at_ms == host.at_ms
    assert server.node == host.node == "sfu-1"


# ---------------------------------------------------------------------------
# Honesty: an unmeasured value is never a zero
# ---------------------------------------------------------------------------


async def test_a_renamed_series_stays_null_and_is_counted_out(storage) -> None:
    """A release that renamed a metric must not draw a flat zero line."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_rooms 3\nsomething_else 9\n")

    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SERVER_TARGET), transport=_transport(handler)
    )
    await worker.tick_now()

    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.outcome == OUTCOME_OK  # the scrape itself was fine
    assert row.series_found == 0  # ...and nothing we expected was there
    assert row.rooms is None
    assert row.packets_total is None


async def test_a_real_zero_is_stored_as_zero(storage) -> None:
    """The counterpart: a target reporting 0 is a measurement, and is kept."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_room_total 0\n")

    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SERVER_TARGET), transport=_transport(handler)
    )
    await worker.tick_now()

    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.rooms == 0
    assert row.series_found == 1


# ---------------------------------------------------------------------------
# A hanging or broken target never stalls the process
# ---------------------------------------------------------------------------


async def test_a_hanging_target_times_out_and_records_the_gap(storage) -> None:
    """The tick must end on its own deadline, with a row that says why."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)  # never answers within the deadline
        return httpx.Response(200, text="livekit_room_total 1\n")

    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET),
        scrape_timeout_seconds=0.15,
        transport=_transport(handler),
    )
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(worker.tick_now(), timeout=5)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 3, "a hanging target held the tick open"

    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.outcome == OUTCOME_TIMEOUT
    assert row.series_found is None
    assert row.rooms is None


async def test_a_hanging_target_does_not_hold_up_a_healthy_one(storage) -> None:
    """Targets are scraped concurrently: one dead node costs one timeout, once."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if "9100" in str(request.url):
            await asyncio.sleep(30)
        return _canned(request)

    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET, HOST_TARGET),
        scrape_timeout_seconds=0.2,
        transport=_transport(handler),
    )
    assert await worker.tick_now() == 2

    assert (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0].rooms == 3
    assert (await _rows(storage, "sfu-1", SOURCE_NODE_EXPORTER))[
        0
    ].outcome == OUTCOME_TIMEOUT


async def test_a_refused_connection_is_recorded_as_unreachable(storage) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SERVER_TARGET), transport=_transport(handler)
    )
    await worker.tick_now()
    assert (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[
        0
    ].outcome == OUTCOME_UNREACHABLE


async def test_a_non_200_is_recorded_as_http_error(storage) -> None:
    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(
            ScrapeTarget(
                node="sfu-9", url="http://nowhere/metrics", source=SOURCE_LIVEKIT_SERVER
            )
        ),
        transport=_transport(_canned),
    )
    await worker.tick_now()
    assert (await _rows(storage, "sfu-9", SOURCE_LIVEKIT_SERVER))[
        0
    ].outcome == OUTCOME_HTTP_ERROR


async def test_an_html_error_page_is_recorded_as_unparseable(storage) -> None:
    """A 200 that is not an exposition is not a node with no traffic."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>gateway</body></html>")

    worker = NodeSamplesWorker(
        storage, target_provider=_provider(SERVER_TARGET), transport=_transport(handler)
    )
    await worker.tick_now()
    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.outcome == OUTCOME_UNPARSEABLE
    assert row.series_found is None


async def test_an_oversized_body_is_dropped_not_buffered(storage) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_room_total 1\n" * 10_000)

    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET),
        max_response_bytes=1024,
        transport=_transport(handler),
    )
    await worker.tick_now()
    row = (await _rows(storage, "sfu-1", SOURCE_LIVEKIT_SERVER))[0]
    assert row.outcome == OUTCOME_TOO_LARGE
    assert row.rooms is None


async def test_one_failing_target_does_not_lose_the_others(storage) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "6789" in str(request.url):
            raise httpx.ConnectError("nope", request=request)
        return _canned(request)

    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET, SIP_TARGET, HOST_TARGET),
        transport=_transport(handler),
    )
    assert await worker.tick_now() == 3
    assert (await _rows(storage, "sip-1", SOURCE_LIVEKIT_SIP))[0].sip_calls_active == 7


# ---------------------------------------------------------------------------
# Retention: the table is self-limiting
# ---------------------------------------------------------------------------


async def test_every_tick_trims_samples_past_the_max_age(storage) -> None:
    """~57k rows/day is unbounded without this, whatever the retention config."""
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await repo.insert_samples(
            db,
            [
                repo.NodeSampleInput(
                    node="sfu-1", source=SOURCE_LIVEKIT_SERVER, at_ms=1, outcome="ok"
                )
            ],
        )
        assert await repo.count_samples(db) == 1

    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET),
        max_age_seconds=60,
        transport=_transport(_canned),
    )
    await worker.tick_now()

    async with storage._conn.session() as db:
        rows = await repo.list_samples(db, node="sfu-1", source=SOURCE_LIVEKIT_SERVER)
    assert [r.at_ms for r in rows] != [1]
    assert len(rows) == 1  # the ancient row is gone, the fresh one stayed


async def test_the_trim_leaves_samples_inside_the_window(storage) -> None:
    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET),
        max_age_seconds=7 * 24 * 3600,
        transport=_transport(_canned),
    )
    await worker.tick_now()
    await worker.tick_now()
    async with storage._conn.session() as db:
        assert await repo.count_samples(db) == 2


# ---------------------------------------------------------------------------
# Lifecycle (mirrors AgentObservationsWorker)
# ---------------------------------------------------------------------------


async def test_defaults_are_the_documented_cadence(storage) -> None:
    worker = NodeSamplesWorker(storage)
    assert worker._poll_interval == 15.0
    assert worker._scrape_timeout == 5.0
    assert worker._max_age_seconds == 7 * 24 * 3600.0


async def test_no_targets_is_a_no_op(storage) -> None:
    worker = NodeSamplesWorker(storage, target_provider=_provider())
    assert await worker.tick_now() == 0


async def test_start_stop_idempotent(storage) -> None:
    worker = NodeSamplesWorker(
        storage,
        target_provider=_provider(SERVER_TARGET),
        poll_interval_seconds=0.05,
        transport=_transport(_canned),
    )
    await worker.start()
    await worker.start()
    await asyncio.sleep(0.15)
    await worker.stop()
    await worker.stop()


async def test_loop_continues_on_tick_exception(storage) -> None:
    async def boom():
        raise RuntimeError("boom")

    worker = NodeSamplesWorker(
        storage, target_provider=boom, poll_interval_seconds=0.05
    )
    await worker.start()
    await asyncio.sleep(0.15)
    await worker.stop()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_interval_seconds": 0},
        {"scrape_timeout_seconds": 0},
        {"max_age_seconds": 0},
        {"max_response_bytes": 0},
        {"trim_batch": 0},
    ],
)
async def test_constructor_validation(storage, kwargs) -> None:
    with pytest.raises(ValueError):
        NodeSamplesWorker(storage, **kwargs)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_targets_from_env_parses_source_name_url() -> None:
    targets = targets_from_env(
        {
            TARGETS_ENV_VAR: (
                "livekit-server:sfu-1=http://10.0.0.4:6789/metrics,"
                " node-exporter:sfu-1=http://10.0.0.4:9100/metrics "
            )
        }
    )
    assert [(t.source, t.node, t.url) for t in targets] == [
        (SOURCE_LIVEKIT_SERVER, "sfu-1", "http://10.0.0.4:6789/metrics"),
        (SOURCE_NODE_EXPORTER, "sfu-1", "http://10.0.0.4:9100/metrics"),
    ]


def test_targets_from_env_is_empty_when_unset() -> None:
    assert targets_from_env({}) == []


def test_a_malformed_entry_is_skipped_not_raised() -> None:
    """A typo must not take down a process that is also serving the dashboard."""
    targets = targets_from_env(
        {
            TARGETS_ENV_VAR: (
                "garbage,"
                "unknown-source:x=http://h/metrics,"
                "livekit-sip:=http://h/metrics,"
                "livekit-sip:sip-1=http://sip-1:8080/metrics"
            )
        }
    )
    assert [t.node for t in targets] == ["sip-1"]


def test_every_mapped_series_names_a_real_value_column() -> None:
    """A map entry naming a column the model lacks would be an empty chart."""
    for source, series in SERIES.items():
        assert source in {
            SOURCE_LIVEKIT_SERVER,
            SOURCE_LIVEKIT_SIP,
            SOURCE_NODE_EXPORTER,
        }
        for entry in series:
            assert entry.column in repo.VALUE_COLUMNS, entry
