"""``series_found`` counting what stored, and the unbounded-ceiling marker.

Two accounting defects, both of which report a row as carrying more than it
does.

**series_found was counted at match time.** The worker incremented it when a
series matched the exposition, and the repository refused some of those values
later at coercion: a non-finite reading, or one that does not survive a 64-bit
column. The count never heard about the refusal, so the row claimed a
measurement it did not hold. It is exactly the unreadable columns that go
missing, which makes the overstatement invisible in the one case it matters:
an operator reading "9 of 9 series matched" next to an empty chart.

**The host descriptor ceiling had two indistinguishable empty states.** VERIFIED
LIVE: ``node_filefd_maximum`` reads 9.223372036854776e+18, whose ``int()`` is
9223372036854775808, exactly ONE past the 64-bit ceiling. The value is dropped
and the column is NULL, correctly. But "this limit cannot be reached" and
"nobody measured this limit" were then the same NULL, and they support opposite
conclusions about a fleet's headroom.
"""

from __future__ import annotations

import httpx
import pytest

from voicegateway.middleware.node_samples_worker_middleware import (
    SOURCE_LIVEKIT_SERVER,
    SOURCE_NODE_EXPORTER,
    NodeSamplesWorker,
    ScrapeTarget,
)
from voicegateway.repository import node_samples_repository as repo
from voicegateway.services.storage_service import StorageService

# fs.file-max as a kernel with no limit reports it, verbatim off a live box.
UNBOUNDED_HOST = """\
node_filefd_allocated 1216
node_filefd_maximum 9.223372036854776e+18
node_load1 1.75
"""

# The same host with a real ceiling, which is the non-vacuous counterpart.
BOUNDED_HOST = """\
node_filefd_allocated 1216
node_filefd_maximum 65536
node_load1 1.75
"""

# Nothing said about descriptors at all.
SILENT_HOST = "node_load1 1.75\n"


@pytest.fixture
async def storage(tmp_path):
    service = StorageService(db_path=str(tmp_path / "accounting.db"))
    try:
        yield service
    finally:
        await service.aclose()


async def _scrape(
    storage,
    body: str,
    *,
    source: str = SOURCE_NODE_EXPORTER,
    node: str = "sfu-1",
):
    """One tick against one canned body, returning the row it wrote.

    ``node`` is a parameter because this table APPENDS: two ticks for one
    node are two rows, and a caller scraping several bodies against one name
    would read the first row back three times and see whatever it wants.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    target = ScrapeTarget(node=node, url=f"http://{node}:9100/metrics", source=source)

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage, target_provider=provider, transport=httpx.MockTransport(handler)
    )
    await worker.tick_now()
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        rows = await repo.list_samples(db, node=node, source=source)
    assert len(rows) == 1, f"{node} was scraped more than once"
    return rows[0]


# --------------------------------------------------------------------------
# series_found counts what landed
# --------------------------------------------------------------------------


async def test_a_value_refused_at_coercion_is_counted_out(storage) -> None:
    """The defect. Three series matched; one did not survive storage."""
    row = await _scrape(storage, UNBOUNDED_HOST)
    assert row.filefd_allocated == 1216
    assert row.load1 == pytest.approx(1.75)
    # Dropped rather than stored one past the 64-bit ceiling.
    assert row.filefd_maximum is None
    # 2, not the 3 that matched.
    assert row.series_found == 2


async def test_the_count_matches_the_columns_that_are_readable(storage) -> None:
    """Stated as the invariant rather than as a number, so it cannot drift.

    Whatever the fixture carries, the count and the populated value columns
    must agree, because the count exists to tell an operator whether an empty
    chart means a rename or a real zero.
    """
    row = await _scrape(storage, UNBOUNDED_HOST)
    populated = sum(
        1
        for column in repo.VALUE_COLUMNS - repo.DERIVED_COLUMNS
        if getattr(row, column) is not None
    )
    assert row.series_found == populated


async def test_a_clean_scrape_is_unaffected(storage) -> None:
    """Non-vacuous: the recount is not just subtracting from everything."""
    row = await _scrape(storage, BOUNDED_HOST)
    assert row.filefd_maximum == 65536
    assert row.series_found == 3


async def test_a_failed_scrape_keeps_a_null_count(storage) -> None:
    """NULL is not zero. No exposition was read, so nothing is known."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    target = ScrapeTarget(
        node="sfu-1", url="http://sfu-1:6789/metrics", source=SOURCE_LIVEKIT_SERVER
    )

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage, target_provider=provider, transport=httpx.MockTransport(handler)
    )
    await worker.tick_now()
    async with storage._conn.session() as db:
        row = (await repo.list_samples(db, node="sfu-1", source=SOURCE_LIVEKIT_SERVER))[
            0
        ]
    assert row.series_found is None
    assert row.series_found != 0


async def test_a_non_finite_value_is_counted_out_too(storage) -> None:
    """The other refusal path. NaN is not a measurement of anything."""
    row = await _scrape(storage, "node_load1 NaN\nnode_filefd_allocated 12\n")
    assert row.load1 is None
    assert row.filefd_allocated == 12
    assert row.series_found == 1


def test_a_derived_marker_is_not_counted_as_a_series() -> None:
    """It is computed here, so counting it would claim the target exposed it."""
    assert "filefd_maximum_unbounded" in repo.DERIVED_COLUMNS
    assert repo.DERIVED_COLUMNS <= repo.VALUE_COLUMNS


async def test_the_recount_holds_for_a_caller_that_is_not_the_worker(
    storage,
) -> None:
    """insert_samples is the layer that knows, so the fix belongs there.

    A caller writing its own count cannot know what coercion will refuse.
    """
    await storage._ensure_initialized()
    async with storage._conn.session() as db:
        await repo.insert_samples(
            db,
            [
                repo.NodeSampleInput(
                    node="sfu-2",
                    source=SOURCE_NODE_EXPORTER,
                    at_ms=1_785_520_800_000,
                    outcome="ok",
                    series_found=2,  # the caller's optimistic count
                    values={"filefd_allocated": 10.0, "load1": float("inf")},
                )
            ],
        )
        row = (await repo.list_samples(db, node="sfu-2", source=SOURCE_NODE_EXPORTER))[
            0
        ]
    assert row.load1 is None
    assert row.series_found == 1


# --------------------------------------------------------------------------
# The unbounded marker: three states, never collapsed
# --------------------------------------------------------------------------


async def test_an_unbounded_ceiling_is_recorded_as_such(storage) -> None:
    row = await _scrape(storage, UNBOUNDED_HOST)
    assert row.filefd_maximum is None  # still unstorable, still NULL
    assert row.filefd_maximum_unbounded == 1


async def test_a_real_ceiling_is_recorded_as_bounded(storage) -> None:
    row = await _scrape(storage, BOUNDED_HOST)
    assert row.filefd_maximum == 65536
    assert row.filefd_maximum_unbounded == 0


async def test_an_unscraped_ceiling_stays_null(storage) -> None:
    """The trap. Unmeasured must never render as 0, which reads as bounded."""
    row = await _scrape(storage, SILENT_HOST)
    assert row.filefd_maximum is None
    assert row.filefd_maximum_unbounded is None
    assert row.filefd_maximum_unbounded != 0


async def test_the_three_states_are_all_distinguishable(storage) -> None:
    """Said once, directly: this is the property, not the three cases above."""
    seen = {
        (await _scrape(storage, body, node=name)).filefd_maximum_unbounded
        for name, body in (
            ("host-unbounded", UNBOUNDED_HOST),
            ("host-bounded", BOUNDED_HOST),
            ("host-silent", SILENT_HOST),
        )
    }
    assert seen == {1, 0, None}


async def test_the_threshold_is_below_the_ceiling_it_guards(storage) -> None:
    """A float64 round trip near 2**63 is lossy in both directions.

    Pinned against the live reading rather than against 2**63 itself, because
    equality with the ceiling is exactly what the round trip destroys.
    """
    from voicegateway.middleware.node_samples_worker_middleware import (
        FILEFD_UNBOUNDED_THRESHOLD,
    )

    assert 9.223372036854776e18 >= FILEFD_UNBOUNDED_THRESHOLD
    assert FILEFD_UNBOUNDED_THRESHOLD < float(2**63)
    # And a limit an operator might really set is nowhere near it.
    assert 1_048_576.0 < FILEFD_UNBOUNDED_THRESHOLD


# --------------------------------------------------------------------------
# The deleted counter stays deleted
# --------------------------------------------------------------------------


def test_the_nack_counter_is_gone_from_every_registration() -> None:
    """VERIFIED LIVE: zero occurrences of "nack" in a real 1.10.1 exposition.

    A column nothing can populate is worse than no column: it is reachable by a
    chart that reads NULL for the life of the deployment.
    """
    from voicegateway.middleware.node_samples_worker_middleware import SERIES
    from voicegateway.models.node_sample_model import NodeSample

    assert not [
        e.metric for entries in SERIES.values() for e in entries if "nack" in e.metric
    ]
    assert "nacks_total" not in repo.VALUE_COLUMNS
    assert "nacks_total" not in NodeSample.model_fields


def test_no_module_docstring_still_advertises_it() -> None:
    """A docstring naming a series that does not exist is a false claim.

    Both of these described ``livekit_nack_total`` as a node counter this
    schema carries, which would send a reader looking for a column that was
    removed because the metric never existed.
    """
    from voicegateway.models import node_sample_model
    from voicegateway.repository import node_correlation_repository

    for module in (node_sample_model, node_correlation_repository):
        assert "nack" not in (module.__doc__ or ""), module.__name__
