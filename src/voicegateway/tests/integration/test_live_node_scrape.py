"""One real scrape, against real exporters, asserted column by column.

Every other test of this worker feeds it a canned body. A canned body can only
encode what we BELIEVE an exporter emits, and the whole point of the columns
this file guards is that several of them were wired against documentation and
were wrong. The bugs that motivated them all passed the fixture-based tests:

* a histogram base name that matches no sample and stores NULL forever
* ten livekit-sip entries scraped from livekit-server, which exports none of them
* ``livekit_nack_total``, which does not exist on 1.10.1 under any name
* ``series_found`` counting a value the repository then refused at coercion

So this runs the real code path against real exporters and asserts what landed.

**Read only, and it never writes the real database.** The scrape is a GET; the
rows go to a SQLite file pytest made for the test and deletes after.

Runs only when the exporters answer on localhost, so it skips everywhere else,
and is marked ``integration`` so the unit matrix (``-m "not integration"``)
deselects it.
"""

from __future__ import annotations

import logging
import socket

import pytest

from voicegateway.middleware.node_samples_worker_middleware import (
    SERIES,
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
    TARGETS_ENV_VAR,
    NodeSamplesWorker,
    targets_from_env,
)
from voicegateway.repository import node_samples_repository as repo
from voicegateway.services.storage_service import StorageService

NODE = "live-1"
HOST_PORT = 9100  # node_exporter
SIP_PORT = 6790  # livekit-sip
SERVER_PORT = 6789  # livekit-server, behind basic auth

# Invented here. The point of pointing an authenticated target at a real server
# is to watch what the logger does with the credential, and a 401 exercises that
# exactly as well as a 200 would.
FAKE_USER = "metrics-reader"
FAKE_SECRET = "not-a-real-password-9271"


def _answers(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_answers(HOST_PORT) and _answers(SIP_PORT)),
        reason=f"no exporter answering on localhost:{HOST_PORT} and :{SIP_PORT}",
    ),
]

TARGETS = (
    f"{SOURCE_NODE_EXPORTER}:{NODE}=http://localhost:{HOST_PORT}/metrics,"
    f"{SOURCE_LIVEKIT_SIP}:{NODE}=http://localhost:{SIP_PORT}/metrics,"
    f"{SOURCE_LIVEKIT_SERVER}:{NODE}="
    f"http://{FAKE_USER}:{FAKE_SECRET}@localhost:{SERVER_PORT}/metrics"
)


@pytest.fixture(scope="module")
async def scraped(tmp_path_factory):
    """One real tick into a throwaway database, shared by every assertion.

    Module-scoped so the exporters are hit once rather than once per test: this
    talks to something outside the process and has no business hammering it.
    """
    db_path = tmp_path_factory.mktemp("live-scrape") / "nodes.db"
    storage = StorageService(db_path=str(db_path))
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        await storage._ensure_initialized()
        worker = NodeSamplesWorker(
            storage, target_provider=lambda: _targets(), transport=None
        )
        written = await worker.tick_now()
        rows = {}
        async with storage._conn.session() as db:
            for source in (
                SOURCE_NODE_EXPORTER,
                SOURCE_LIVEKIT_SIP,
                SOURCE_LIVEKIT_SERVER,
            ):
                found = await repo.list_samples(db, node=NODE, source=source)
                rows[source] = found[0] if found else None
        yield {"written": written, "rows": rows, "records": records}
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
        await storage.aclose()


async def _targets():
    return targets_from_env({TARGETS_ENV_VAR: TARGETS})


def _populated(row) -> set[str]:
    """Value columns actually carrying something, excluding derived markers."""
    return {
        column
        for column in repo.VALUE_COLUMNS - repo.DERIVED_COLUMNS
        if getattr(row, column) is not None
    }


# --------------------------------------------------------------------------
# The scrape happened at all
# --------------------------------------------------------------------------


async def test_every_target_produced_a_row(scraped) -> None:
    assert scraped["written"] == 3
    assert all(row is not None for row in scraped["rows"].values())


# --------------------------------------------------------------------------
# series_found describes what STORED, not what matched
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", [SOURCE_NODE_EXPORTER, SOURCE_LIVEKIT_SIP])
async def test_the_count_equals_the_columns_that_landed(scraped, source) -> None:
    """The defect this file is best placed to catch.

    It only appears when a real exporter emits a value the repository refuses,
    which no hand-written fixture would think to contain.
    """
    row = scraped["rows"][source]
    assert row.outcome == "ok"
    assert row.series_found == len(_populated(row)), source


async def test_the_host_ceiling_is_the_one_that_gets_refused(scraped) -> None:
    """Live proof of the drop, and of the count that used to ignore it.

    node_filefd_maximum on this box does not survive a float64 round trip, so
    the column is NULL while every sibling stores fine. Before the recount,
    series_found would have claimed one more series than the row holds.
    """
    row = scraped["rows"][SOURCE_NODE_EXPORTER]
    assert row.filefd_maximum is None
    assert row.filefd_allocated is not None
    matched = len(SERIES[SOURCE_NODE_EXPORTER])
    assert row.series_found == matched - 1


async def test_a_401_records_a_null_count_not_a_zero(scraped) -> None:
    """No exposition was read, so nothing is known about how many series exist."""
    row = scraped["rows"][SOURCE_LIVEKIT_SERVER]
    assert row.outcome == "http_error"
    assert row.series_found is None
    assert _populated(row) == set()


# --------------------------------------------------------------------------
# The unbounded marker, against a kernel that really is unbounded
# --------------------------------------------------------------------------


async def test_the_ceiling_is_recorded_as_unbounded(scraped) -> None:
    """1, not NULL. The value was measured; it just cannot be stored."""
    assert scraped["rows"][SOURCE_NODE_EXPORTER].filefd_maximum_unbounded == 1


async def test_the_marker_stays_null_where_the_series_does_not_exist(
    scraped,
) -> None:
    """livekit-sip exports no host filefd pair, so it says nothing about one."""
    assert scraped["rows"][SOURCE_LIVEKIT_SIP].filefd_maximum_unbounded is None


# --------------------------------------------------------------------------
# Every wired series resolves against the running binary
# --------------------------------------------------------------------------


async def test_every_livekit_sip_series_landed(scraped) -> None:
    """A name that matches nothing stores NULL for the life of a deployment.

    Nothing at runtime says so, which is why this is asserted against a real
    binary rather than inferred from documentation.
    """
    row = scraped["rows"][SOURCE_LIVEKIT_SIP]
    expected = {entry.column for entry in SERIES[SOURCE_LIVEKIT_SIP]}
    assert _populated(row) == expected


async def test_the_headroom_pair_is_real(scraped) -> None:
    """The per-process rlimit, which is what the headroom gate judges."""
    row = scraped["rows"][SOURCE_LIVEKIT_SIP]
    assert row.process_open_fds is not None
    assert row.process_max_fds is not None
    assert 0 < row.process_open_fds < row.process_max_fds


async def test_the_join_histogram_is_stored_as_sum_and_count(scraped) -> None:
    """Not as summed buckets, which counts one observation once per bucket."""
    row = scraped["rows"][SOURCE_LIVEKIT_SIP]
    assert row.sip_join_sec_count is not None
    assert row.sip_join_sec_sum is not None
    # Every bucket count is bounded by the total, which a summed value is not.
    assert row.sip_join_le1_count <= row.sip_join_sec_count
    assert row.sip_join_le5_count <= row.sip_join_sec_count


async def test_rtp_is_split_by_direction(scraped) -> None:
    """One-way audio must not disappear into a single total."""
    row = scraped["rows"][SOURCE_LIVEKIT_SIP]
    assert row.sip_rtp_packets_recv is not None
    assert row.sip_rtp_packets_send is not None


# --------------------------------------------------------------------------
# Nothing leaked across sources
# --------------------------------------------------------------------------


async def test_no_source_carries_another_source_columns(scraped) -> None:
    """An entry in the wrong tuple reads exactly like a name that does not exist.

    Asserted as a disjointness property so it holds for entries added later.
    """
    host = _populated(scraped["rows"][SOURCE_NODE_EXPORTER])
    sip = _populated(scraped["rows"][SOURCE_LIVEKIT_SIP])
    sip_only = {e.column for e in SERIES[SOURCE_LIVEKIT_SIP]} - {
        e.column for e in SERIES[SOURCE_NODE_EXPORTER]
    }
    assert not (host & sip_only)
    assert "filefd_allocated" in host
    assert "filefd_allocated" not in sip


async def test_the_deleted_nack_column_is_not_reachable(scraped) -> None:
    row = scraped["rows"][SOURCE_LIVEKIT_SIP]
    assert not hasattr(row, "nacks_total")


# --------------------------------------------------------------------------
# The credential, against a server that really challenges for it
# --------------------------------------------------------------------------


async def test_no_log_line_carries_the_credential(scraped) -> None:
    """The live counterpart of the unit test, over every logger in the process.

    Includes the HTTP client's own request line, which is where the password
    used to be written four times a minute.
    """
    assert scraped["records"], "nothing was logged, so this proves nothing"
    for record in scraped["records"]:
        assert FAKE_SECRET not in record.getMessage(), record.name


async def test_the_request_line_was_still_logged(scraped) -> None:
    """Non-vacuous: the credential is absent because it was removed from the
    URL, not because the line that would have carried it was suppressed."""
    lines = [r.getMessage() for r in scraped["records"] if r.name == "httpx"]
    assert any(f"localhost:{SERVER_PORT}" in line for line in lines), lines
    assert any(f"localhost:{SIP_PORT}" in line for line in lines), lines
