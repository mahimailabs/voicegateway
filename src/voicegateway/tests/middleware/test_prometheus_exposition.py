"""The exposition parser: labels, escapes, and absent-is-not-zero.

The parser is pure text in / samples out, so everything here is a canned
exposition body. Nothing in this file touches the network.
"""

from __future__ import annotations

from voicegateway.middleware.prometheus_exposition import (
    parse_exposition,
    sum_series,
)


def test_parses_bare_and_labelled_samples() -> None:
    body = """
# HELP livekit_room_total number of rooms
# TYPE livekit_room_total gauge
livekit_room_total 3
livekit_packet_total{direction="incoming",transmission="rtp"} 120
livekit_packet_total{direction="outgoing",transmission="rtp"} 80
"""
    samples = parse_exposition(body)
    assert len(samples) == 3
    assert samples[0].name == "livekit_room_total"
    assert samples[0].labels == {}
    assert samples[0].value == 3.0
    assert samples[1].labels["direction"] == "incoming"


def test_help_and_type_lines_are_skipped() -> None:
    body = "# HELP x help\n# TYPE x counter\nx 1\n"
    assert [s.name for s in parse_exposition(body)] == ["x"]


def test_scientific_notation_and_negative_values() -> None:
    body = "a 1.5e+06\nb -2\n"
    values = {s.name: s.value for s in parse_exposition(body)}
    assert values == {"a": 1_500_000.0, "b": -2.0}


def test_nan_and_inf_are_not_measurements() -> None:
    """An exporter emits NaN for "no data"; storing it would draw a fake point."""
    body = "a NaN\nb +Inf\nc -Inf\nd 1\n"
    assert [s.name for s in parse_exposition(body)] == ["d"]


def test_optional_exposition_timestamp_is_ignored() -> None:
    """We stamp the scrape ourselves so one tick shares one instant."""
    samples = parse_exposition('x{k="v"} 7 1666000000000\n')
    assert samples[0].value == 7.0


def test_label_value_may_contain_a_closing_brace() -> None:
    """A regex/`find` that took the first `}` would truncate the labels here."""
    samples = parse_exposition('x{reason="oops }",k="v"} 4\n')
    assert samples[0].labels == {"reason": "oops }", "k": "v"}
    assert samples[0].value == 4.0


def test_escaped_quotes_and_newlines_in_label_values() -> None:
    samples = parse_exposition('x{msg="a \\"quoted\\" b",nl="c\\nd"} 1\n')
    assert samples[0].labels["msg"] == 'a "quoted" b'
    assert samples[0].labels["nl"] == "c\nd"


def test_one_malformed_line_does_not_lose_the_rest() -> None:
    body = 'good_a 1\nbroken{unclosed="x" 2\nbroken2 \ngood_b 3\n'
    assert [s.name for s in parse_exposition(body)] == ["good_a", "good_b"]


def test_a_non_exposition_document_yields_nothing() -> None:
    """An HTML error page must parse to zero samples, not to junk metrics."""
    assert parse_exposition("<html><body>404</body></html>") == []


def test_sum_series_adds_every_label_combination() -> None:
    samples = parse_exposition(
        'p{direction="incoming"} 10\n'
        'p{direction="outgoing"} 4\n'
        'p{direction="outgoing",country="ca"} 1\n'
    )
    assert sum_series(samples, "p") == 15.0


def test_sum_series_returns_none_for_an_absent_series() -> None:
    """None, never 0.0: "not exposed" and "read zero" are different facts."""
    samples = parse_exposition("p 1\n")
    assert sum_series(samples, "not_there") is None


def test_sum_series_returns_zero_for_a_real_zero() -> None:
    samples = parse_exposition("p 0\n")
    assert sum_series(samples, "p") == 0.0


def test_sum_series_where_filters_on_a_label_value() -> None:
    samples = parse_exposition(
        'node_cpu_seconds_total{cpu="0",mode="idle"} 100\n'
        'node_cpu_seconds_total{cpu="1",mode="idle"} 200\n'
        'node_cpu_seconds_total{cpu="0",mode="user"} 5\n'
    )
    assert sum_series(samples, "node_cpu_seconds_total") == 305.0
    assert (
        sum_series(samples, "node_cpu_seconds_total", where={"mode": "idle"}) == 300.0
    )


def test_sum_series_where_matching_nothing_is_none_not_zero() -> None:
    samples = parse_exposition('node_cpu_seconds_total{mode="user"} 5\n')
    assert sum_series(samples, "node_cpu_seconds_total", where={"mode": "idle"}) is None


# --------------------------------------------------------------------------
# exclude: sum every label value EXCEPT a named one
# --------------------------------------------------------------------------
#
# The shape `where` cannot express. A node's real NIC is enp39s0 on one host and
# ens5 on another, so the devices to KEEP cannot be named; loopback is lo
# everywhere, so the one to drop can be.


def _nic_body() -> str:
    return (
        'node_network_receive_bytes_total{device="lo"} 1000\n'
        'node_network_receive_bytes_total{device="enp39s0"} 40\n'
        'node_network_receive_bytes_total{device="ens5"} 2\n'
    )


def test_sum_series_exclude_drops_the_matching_series_and_sums_the_rest() -> None:
    samples = parse_exposition(_nic_body())
    # Non-vacuous: loopback dominates the unfiltered total, so an exclusion that
    # did nothing would be visible rather than coincidentally right.
    assert sum_series(samples, "node_network_receive_bytes_total") == 1042.0
    assert (
        sum_series(
            samples, "node_network_receive_bytes_total", exclude={"device": "lo"}
        )
        == 42.0
    )


def test_sum_series_exclude_of_an_absent_label_value_changes_nothing() -> None:
    """A host whose loopback is spelled differently still gets a real total.

    The counterpart of the pinning failure: an exclusion that matches nothing
    over-counts by one interface, which is a wrong number. A `where` that
    matches nothing stores NULL forever, which is a missing column. Excluding is
    the safer direction, and this pins that it degrades that way.
    """
    samples = parse_exposition(_nic_body())
    assert (
        sum_series(
            samples, "node_network_receive_bytes_total", exclude={"device": "lo0"}
        )
        == 1042.0
    )


def test_sum_series_exclude_needs_every_pair_to_match() -> None:
    """ALL pairs, not any: more keys excludes LESS, never more."""
    samples = parse_exposition(
        'x{device="lo",kind="virtual"} 5\nx{device="lo",kind="real"} 3\n'
    )
    assert sum_series(samples, "x", exclude={"device": "lo"}) is None
    assert sum_series(samples, "x", exclude={"device": "lo", "kind": "virtual"}) == 3.0
    # A pair whose key is absent from the sample cannot match, so nothing drops.
    assert sum_series(samples, "x", exclude={"device": "lo", "nope": "1"}) == 8.0


def test_sum_series_exclude_and_where_apply_together() -> None:
    samples = parse_exposition(
        'x{device="lo",mode="idle"} 100\n'
        'x{device="ens5",mode="idle"} 7\n'
        'x{device="ens5",mode="user"} 500\n'
    )
    assert (
        sum_series(samples, "x", where={"mode": "idle"}, exclude={"device": "lo"})
        == 7.0
    )


def test_sum_series_exclude_that_removes_everything_is_none_not_zero() -> None:
    """The absent-is-not-zero rule survives exclusion.

    A node exposing only loopback has not reported zero bytes on the wire; it
    has reported nothing about the wire at all, and the column must stay NULL so
    a reader suppresses it rather than drawing a flat healthy line.
    """
    samples = parse_exposition('node_network_receive_bytes_total{device="lo"} 900\n')
    assert (
        sum_series(
            samples, "node_network_receive_bytes_total", exclude={"device": "lo"}
        )
        is None
    )


def test_sum_series_exclude_leaves_an_absent_series_none() -> None:
    samples = parse_exposition('node_network_receive_bytes_total{device="lo"} 1\n')
    assert sum_series(samples, "not_there", exclude={"device": "lo"}) is None


def test_sum_series_default_exclude_keeps_every_sample() -> None:
    """The 51 existing entries pass no exclude, so the default must be inert.

    An empty mapping matches vacuously, so an unguarded `all()` would read it as
    "drop everything" and turn a whole column NULL.
    """
    samples = parse_exposition(_nic_body())
    unfiltered = sum_series(samples, "node_network_receive_bytes_total")
    assert (
        sum_series(samples, "node_network_receive_bytes_total", exclude=None)
        == unfiltered
    )
    assert (
        sum_series(samples, "node_network_receive_bytes_total", exclude={})
        == unfiltered
    )
