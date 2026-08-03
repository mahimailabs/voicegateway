"""Where each half of the lifecycle pair comes from, and why it is not one source.

A real eight-host fleet run produced 26 UNKNOWN lifecycle rows on a deployment
where nothing had gone wrong. Every one traced to the same shape: the two series
the gate needs were wired to COMPLEMENTARY sources, so each subject was missing
exactly the half the other had.

* ``node-exporter`` carried ``node_vmstat_oom_kill`` and no
  ``process_start_time_seconds``, so all 16 node-exporter subjects reported that
  a restart could not be ruled out.
* ``livekit-sip`` and ``livekit-server`` carried ``process_start_time_seconds``
  and no OOM counter, so all 8 reported that an OOM kill could not be ruled out.

The second half is not a scrape-configuration mistake and no amount of
configuring would have fixed it: ``node_vmstat_oom_kill`` is a KERNEL counter
for the whole box, only node_exporter publishes it, and livekit-sip never will
because it is not livekit-sip's to report.
"""

from __future__ import annotations

from voicegateway.livekit_diag import gates
from voicegateway.middleware.node_samples_worker_middleware import (
    SERIES,
    SOURCE_LIVEKIT_SERVER,
    SOURCE_LIVEKIT_SIP,
    SOURCE_NODE_EXPORTER,
    SOURCE_REDIS_EXPORTER,
    columns_for,
)

# --------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------


def test_node_exporter_carries_its_own_start_time() -> None:
    """Read off a live prom/node-exporter at 1.78577024616e+09.

    A node-exporter restart is worth gating in its own right: while it is down
    nothing about that host is scraped, so the restart is a hole in every other
    measurement taken from it.
    """
    assert "process_start_time_seconds" in columns_for(SOURCE_NODE_EXPORTER)


def test_every_scraped_service_can_answer_the_restart_question() -> None:
    """Restarts are per-process, so each source needs its OWN start time."""
    for source in (SOURCE_NODE_EXPORTER, SOURCE_LIVEKIT_SIP, SOURCE_LIVEKIT_SERVER):
        assert "process_start_time_seconds" in columns_for(source), source


def test_only_node_exporter_claims_the_kernel_oom_counter() -> None:
    """It is a property of the box, and wiring it elsewhere would be a guess.

    This is the assertion that keeps the aggregation fix honest: if someone
    "fixes" the livekit UNKNOWNs by adding node_vmstat_oom_kill to those
    sources, the column stores NULL for the life of the deployment while
    series_found still reads high, which is the exact failure the map's
    provenance rule exists to prevent.
    """
    assert "vmstat_oom_kill" in columns_for(SOURCE_NODE_EXPORTER)
    for source in (SOURCE_LIVEKIT_SIP, SOURCE_LIVEKIT_SERVER, SOURCE_REDIS_EXPORTER):
        assert "vmstat_oom_kill" not in columns_for(source), source


def test_redis_exporter_start_time_stays_unwired() -> None:
    """The exporter publishes it; it is still not Redis's.

    Redis here is ElastiCache and its process is invisible to the scrape set, so
    "did Redis restart" is unmeasurable. Wiring the sidecar's own start time
    would silence the UNKNOWN by answering a different question, which is the
    category error this source's own docstring pins for process_open_fds.
    """
    assert "process_start_time_seconds" not in columns_for(SOURCE_REDIS_EXPORTER)
    assert SERIES[SOURCE_REDIS_EXPORTER]


# --------------------------------------------------------------------------
# What the gate does with the pair
# --------------------------------------------------------------------------


def _reading(source: str, *, starts, ooms) -> gates.LifecycleReading:
    return gates.LifecycleReading(
        node="sip-1", source=source, start_times=tuple(starts), oom_kills=tuple(ooms)
    )


def test_a_livekit_subject_with_a_borrowed_oom_series_is_gated_not_unknown() -> None:
    """The point of the aggregation change, expressed at the gate.

    Given the node's OOM counter alongside livekit-sip's own start times, the
    subject is answerable. Before, the OOM tuple was empty and the row read
    "carried no node_vmstat_oom_kill, so an OOM kill could not be ruled out".
    """
    result = gates.process_lifecycle_gate(
        _reading(SOURCE_LIVEKIT_SIP, starts=[100.0, 100.0, 100.0], ooms=[0.0, 0.0, 0.0])
    )
    assert result.status == gates.PASS


def test_a_borrowed_oom_series_that_rose_still_fails_the_subject() -> None:
    """Borrowing the series must not soften it.

    The kernel killing something on the host while livekit-sip ran there is
    evidence bearing on livekit-sip's run whether or not livekit-sip was the
    victim, which is why restarts and OOM are two signals and not one.
    """
    result = gates.process_lifecycle_gate(
        _reading(SOURCE_LIVEKIT_SIP, starts=[100.0, 100.0], ooms=[0.0, 3.0])
    )
    assert result.status == gates.FAIL


def test_a_subject_on_a_node_with_no_node_exporter_stays_unknown() -> None:
    """Nothing is invented when the host is not scraped for it."""
    result = gates.process_lifecycle_gate(
        _reading(SOURCE_LIVEKIT_SIP, starts=[100.0, 100.0], ooms=[])
    )
    assert result.status == gates.UNKNOWN
