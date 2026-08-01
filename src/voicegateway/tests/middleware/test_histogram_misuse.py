"""Two ways a metric map produces a believable wrong number, made impossible.

Both were observed against a real livekit-sip 1.10.1 exposition, and the
fixture beside this test is that capture with the hostname and node id
replaced. A hand-written two-line exposition would not have caught either:
what makes them dangerous is that they occur among 327 samples where
everything else works.

**Summing cumulative buckets.** Prometheus buckets are inclusive of every
smaller bound, so adding them counts one observation once per bucket. On this
capture the join histogram sums to 11.0 against a true ``_count`` of 1. At 500
concurrent that is a smooth, plausible curve.

**Naming the histogram base.** ``livekit_sip_dur_join_sec`` carries no sample of
its own; only its ``_bucket``, ``_sum`` and ``_count`` do. An entry written
against the base stores NULL for the life of the deployment, while
``series_found`` still reads high because the sibling entries matched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voicegateway.middleware.node_samples_worker_middleware import (
    SERIES,
    _Series,
    validate_series_map,
)
from voicegateway.middleware.prometheus_exposition import (
    HistogramMisuse,
    histogram_bases,
    parse_exposition,
    sum_series,
)

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / ("livekit_sip_exposition.txt")
)


@pytest.fixture(scope="module")
def live():
    """A real capture, not a contrivance. 327 samples across 121 families."""
    return parse_exposition(FIXTURE.read_text())


def test_the_fixture_is_a_real_capture(live) -> None:
    """Guards the premise. A trimmed fixture would weaken every test below."""
    assert len(live) > 300
    names = {s.name for s in live}
    assert len(names) > 100
    # The families the engagement actually turns on.
    for name in (
        "livekit_sip_dur_join_sec_bucket",
        "livekit_sip_dur_join_sec_count",
        "livekit_sip_packets_rtp",
        "livekit_sip_available",
    ):
        assert name in names, name


# --------------------------------------------------------------------------
# Buckets
# --------------------------------------------------------------------------


def test_summing_buckets_is_refused(live) -> None:
    with pytest.raises(HistogramMisuse) as excinfo:
        sum_series(live, "livekit_sip_dur_join_sec_bucket")
    assert "cumulative" in str(excinfo.value)
    assert "le" in str(excinfo.value)


def test_the_refused_sum_would_have_been_eleven_times_the_truth(live) -> None:
    """Pins the wrong answer, so this test cannot pass by coincidence.

    Reconstructed the way the old code did it, by summing the raw samples,
    which is exactly what sum_series now refuses to do.
    """
    naive = sum(s.value for s in live if s.name == "livekit_sip_dur_join_sec_bucket")
    true_count = sum_series(live, "livekit_sip_dur_join_sec_count")
    assert naive == 11.0
    assert true_count == 1.0
    assert naive == 11 * true_count


@pytest.mark.parametrize("le", ["0.1", "1", "5", "+Inf"])
def test_an_explicit_le_selects_one_bucket(live, le: str) -> None:
    value = sum_series(live, "livekit_sip_dur_join_sec_bucket", where={"le": le})
    assert value is not None
    # Every bucket is at most the total count, which a summed value is not.
    assert value <= sum_series(live, "livekit_sip_dur_join_sec_count")


def test_the_inf_bucket_equals_the_count(live) -> None:
    """The one identity that proves the le selector is reading real buckets."""
    assert sum_series(
        live, "livekit_sip_dur_join_sec_bucket", where={"le": "+Inf"}
    ) == sum_series(live, "livekit_sip_dur_join_sec_count")


# --------------------------------------------------------------------------
# Summing across LABELS is a different operation and must still work
# --------------------------------------------------------------------------


def test_label_summing_is_untouched(live) -> None:
    """node_cpu_seconds_total and packets_rtp both depend on it.

    Buckets and labels shared one code path, and the fix must not have taken
    the legitimate one with it.
    """
    recv = sum_series(live, "livekit_sip_packets_rtp", where={"op": "recv"})
    send = sum_series(live, "livekit_sip_packets_rtp", where={"op": "send"})
    total = sum_series(live, "livekit_sip_packets_rtp")
    assert (recv, send) == (330.0, 337.0)
    assert total == recv + send == 667.0


def test_an_absent_series_is_still_none_not_zero(live) -> None:
    assert sum_series(live, "livekit_metric_that_does_not_exist") is None


# --------------------------------------------------------------------------
# Base histogram names
# --------------------------------------------------------------------------


def test_the_base_name_matches_nothing(live) -> None:
    """The silent failure. It reads as an absent series, not as a mistake."""
    assert sum_series(live, "livekit_sip_dur_join_sec") is None
    assert sum_series(live, "livekit_sip_dur_check_sec") is None


def test_histogram_bases_are_detectable(live) -> None:
    bases = histogram_bases(live)
    assert "livekit_sip_dur_join_sec" in bases
    assert "livekit_sip_dur_check_sec" in bases
    # A plain counter is not a base and must not be flagged.
    assert "livekit_sip_packets_rtp" not in bases


def test_no_series_entry_names_a_histogram_base(live) -> None:
    """The check that cannot be made statically, run against real data.

    An entry naming a base stores NULL forever while series_found reads high,
    so nothing at runtime would ever surface it.
    """
    bases = histogram_bases(live)
    offenders = [
        f"{source}: {entry.metric} -> {entry.column}"
        for source, entries in SERIES.items()
        for entry in entries
        if entry.metric in bases
    ]
    assert not offenders, f"SERIES entries naming a histogram base: {offenders}"


# --------------------------------------------------------------------------
# The map is validated at import
# --------------------------------------------------------------------------


def test_the_shipped_map_validates() -> None:
    validate_series_map(SERIES)


def test_a_bucket_entry_without_an_le_is_refused() -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_series_map({"src": (_Series("livekit_sip_dur_join_sec_bucket", "c"),)})
    assert "le selector" in str(excinfo.value)
    assert "livekit_sip_dur_join_sec_bucket" in str(excinfo.value)


def test_a_bucket_entry_with_an_le_is_accepted() -> None:
    """Non-vacuous: the guard blocks the mistake, not every bucket entry."""
    validate_series_map(
        {"src": (_Series("livekit_sip_dur_join_sec_bucket", "c", where={"le": "1"}),)}
    )


def test_the_map_is_validated_at_import_not_on_demand() -> None:
    """A bad map must be a startup failure, not a column that stores garbage."""
    import inspect

    from voicegateway.middleware import node_samples_worker_middleware as module

    source = inspect.getsource(module)
    assert "\nvalidate_series_map(SERIES)\n" in source


# --------------------------------------------------------------------------
# Every wired livekit-sip entry resolves against the real capture
# --------------------------------------------------------------------------


def test_every_sip_entry_resolves_against_the_capture(live) -> None:
    """The point of wiring against a live box rather than documentation.

    A name that matches nothing stores NULL for the life of a deployment, and
    nothing at runtime says so. This is the only place that can catch it.
    """
    absent = [
        f"{entry.metric} -> {entry.column}"
        for entry in SERIES["livekit-sip"]
        if sum_series(live, entry.metric, where=entry.where) is None
    ]
    assert not absent, f"mapped names absent from the capture: {absent}"


def test_the_sip_tuple_holds_no_server_only_name(live) -> None:
    """Placement is as easy to get wrong as the name itself.

    An entry in the wrong tuple is scraped against a binary that does not
    export it, which reads exactly like a name that does not exist.
    """
    for entry in SERIES["livekit-sip"]:
        assert sum_series(live, entry.metric, where=entry.where) is not None


def test_livekit_node_cpu_load_is_sip_only() -> None:
    """It exists on livekit-sip and on none of the server's livekit_* families."""
    server = {e.metric for e in SERIES["livekit-server"]}
    sip = {e.metric for e in SERIES["livekit-sip"]}
    assert "livekit_node_cpu_load" in sip
    assert "livekit_node_cpu_load" not in server


def test_no_sip_named_series_is_scraped_from_the_server() -> None:
    leaked = [
        e.metric for e in SERIES["livekit-server"] if e.metric.startswith("livekit_sip")
    ]
    assert not leaked, f"livekit-sip names in the server tuple: {leaked}"


def test_rtp_is_split_by_direction_and_never_summed(live) -> None:
    """One-way audio must not disappear into a single total.

    A call that sent 337 and received 330 sums to 667, and a fleet losing its
    inbound leg would look identical to a healthy one at that granularity.
    """
    entries = {
        e.column: e
        for e in SERIES["livekit-sip"]
        if e.metric == "livekit_sip_packets_rtp"
    }
    assert set(entries) == {"sip_rtp_packets_recv", "sip_rtp_packets_send"}
    for entry in entries.values():
        assert entry.where and "op" in entry.where
        # NOT filtered on payload: the send leg carries the literal "audio"
        # rather than a codec name, so a payload filter would empty it.
        assert "payload" not in entry.where
    recv = sum_series(live, "livekit_sip_packets_rtp", where={"op": "recv"})
    send = sum_series(live, "livekit_sip_packets_rtp", where={"op": "send"})
    assert recv != send, "the fixture cannot distinguish the two directions"


def test_the_unverifiable_server_names_were_not_guessed_in() -> None:
    """Their columns exist and store NULL, which is the honest state.

    A guessed name would store NULL for the life of a deployment while
    series_found still read high because its siblings matched.
    """
    wired = {e.metric for entries in SERIES.values() for e in entries}
    for name in (
        "livekit_session_start_time_ms_sum",
        "livekit_session_start_time_ms_count",
        "livekit_track_published_total",
        "livekit_track_subscribed_total",
        "psrpc_stream_count",
    ):
        assert name not in wired, f"{name} was guessed into the map"
