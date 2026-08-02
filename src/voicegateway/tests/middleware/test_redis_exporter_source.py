"""redis_exporter as a scrape source, and the authority it does NOT carry.

"No sustained Redis or health-check failures" is an acceptance criterion that
produced no gate at all: not a pass, not a fail, not even an UNKNOWN, because
nothing scraped Redis.

Every series name below was read off a live oliver006/redis_exporter scraping
redis:7 before it was wired. The module docstring's provenance rule is that
nothing in SERIES is inferred, and a guessed name stores NULL for the life of a
deployment while series_found still reads high.

**The trap this file exists to pin.** redis_exporter publishes its own
``process_open_fds`` 8 against ``process_max_fds`` 1048576. It is entirely
capable of the measurement and is the authority for none of it: those are a
monitoring sidecar's file handles, not any service's headroom. Two commits were
spent removing exactly that category error from node-exporter, and re-creating
it in a new source would be the same defect with a different name.
"""

from __future__ import annotations

from voicegateway.middleware.node_samples_worker_middleware import (
    DEPENDENCY_EXPORTERS,
    HOST_EXPORTERS,
    SERIES,
    SERVICE_EXPORTERS,
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
    SOURCE_REDIS_EXPORTER,
    _mark_unbounded_redis_memory,
    columns_for,
    reports_host_metrics,
    reports_process_metrics,
)
from voicegateway.repository.node_samples_repository import (
    COUNTER_COLUMNS,
    DERIVED_COLUMNS,
    GAUGE_COLUMNS,
    VALUE_COLUMNS,
)

# Read off the live exporter. Kept as data so the provenance is in the test.
OBSERVED = {
    "redis_up": 1.0,
    "redis_rejected_connections_total": 0.0,
    "redis_memory_used_bytes": 1194336.0,
    "redis_memory_max_bytes": 0.0,
    "redis_blocked_clients": 0.0,
    "redis_evicted_keys_total": 0.0,
}


# --------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------


def test_the_source_is_declared() -> None:
    assert SOURCE_REDIS_EXPORTER in SERIES
    assert SERIES[SOURCE_REDIS_EXPORTER]


def test_every_wired_column_is_one_the_live_exporter_produced() -> None:
    """Nothing here is inferred, which is the map's own rule."""
    assert columns_for(SOURCE_REDIS_EXPORTER) == set(OBSERVED)


def test_the_metric_names_equal_the_column_names() -> None:
    """redis_exporter needs no renaming, unlike node_exporter's node_ prefix."""
    for entry in SERIES[SOURCE_REDIS_EXPORTER]:
        assert entry.metric == entry.column


def test_it_is_the_smallest_set_that_answers_the_criterion() -> None:
    """The exporter publishes 302 redis_* series. Six are wired.

    Not a style point: every column is one somebody has to keep honest, and the
    criterion asks whether Redis failed, not for everything Redis knows.
    """
    assert len(SERIES[SOURCE_REDIS_EXPORTER]) == 6


def test_every_column_is_classified_exactly_once() -> None:
    for column in columns_for(SOURCE_REDIS_EXPORTER):
        assert column in VALUE_COLUMNS
        assert (column in COUNTER_COLUMNS) != (column in GAUGE_COLUMNS)


def test_the_two_cumulative_series_are_counters() -> None:
    """An absolute "3 rejected" says nothing about THIS window."""
    assert "redis_rejected_connections_total" in COUNTER_COLUMNS
    assert "redis_evicted_keys_total" in COUNTER_COLUMNS


# --------------------------------------------------------------------------
# Authority: what redis_exporter does NOT speak for
# --------------------------------------------------------------------------


def test_it_is_not_the_authority_for_a_service_process(  # the headline trap
) -> None:
    assert reports_process_metrics(SOURCE_REDIS_EXPORTER) is False


def test_its_own_descriptor_pair_is_deliberately_unwired() -> None:
    """Capability is not authority. It publishes them; we do not store them."""
    assert "process_open_fds" not in columns_for(SOURCE_REDIS_EXPORTER)
    assert "process_max_fds" not in columns_for(SOURCE_REDIS_EXPORTER)


def test_it_cannot_report_node_wide_facts() -> None:
    """VERIFIED BY SCRAPE: zero node_* series on a live redis_exporter."""
    assert reports_host_metrics(SOURCE_REDIS_EXPORTER) is False


def test_a_host_exporter_is_still_not_suppressed_for_process_fds() -> None:
    """The regression guard, and the reason this is three-valued.

    node_exporter answers None rather than False. It publishes the pair and is
    wired for it, and answering False here would suppress a gate for a metric it
    does produce, which is the defect two earlier commits exist to prevent.
    """
    assert reports_process_metrics(SOURCE_NODE_EXPORTER) is None


def test_the_services_under_test_are_still_the_authority() -> None:
    for source in (SOURCE_LIVEKIT_SIP, SOURCE_LIVEKIT_SERVER):
        assert reports_process_metrics(source) is True


def test_the_three_sets_do_not_overlap() -> None:
    assert not HOST_EXPORTERS & SERVICE_EXPORTERS
    assert not HOST_EXPORTERS & DEPENDENCY_EXPORTERS
    assert not SERVICE_EXPORTERS & DEPENDENCY_EXPORTERS


# --------------------------------------------------------------------------
# A maximum of zero is "no limit", not "no memory"
# --------------------------------------------------------------------------


def test_a_zero_maximum_is_recorded_as_unbounded() -> None:
    """What the live exporter actually reported, and the trap in it.

    redis:7 with no maxmemory answers redis_memory_max_bytes 0. Dividing used by
    that is either a crash or, once guarded, a number reading as total
    exhaustion on the one chart somebody checks during an incident.
    """
    values: dict[str, float | None] = dict(OBSERVED)
    _mark_unbounded_redis_memory(values)
    assert values["redis_memory_max_unbounded"] == 1.0


def test_a_real_maximum_is_not_marked_unbounded() -> None:
    values: dict[str, float | None] = {"redis_memory_max_bytes": 4294967296.0}
    _mark_unbounded_redis_memory(values)
    assert values["redis_memory_max_unbounded"] == 0.0
    assert values["redis_memory_max_bytes"] == 4294967296.0


def test_an_absent_maximum_stays_null_and_is_never_zero() -> None:
    """The third state. Unmeasured is not "unbounded" and is not "bounded"."""
    values: dict[str, float | None] = {"redis_memory_used_bytes": 1194336.0}
    _mark_unbounded_redis_memory(values)
    assert "redis_memory_max_unbounded" not in values


def test_the_marker_is_derived_and_not_counted_as_a_series() -> None:
    """series_found answers what the TARGET exposed. A derived marker would
    inflate it into a claim about the exposition."""
    assert "redis_memory_max_unbounded" in DERIVED_COLUMNS
    assert "redis_memory_max_unbounded" not in columns_for(SOURCE_REDIS_EXPORTER)
