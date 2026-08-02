"""Contracted thresholds, as the HTML states them to a client.

Both the observed value and the threshold went through ``_num(..., "", 1)``, one
decimal place, which is fine for milliseconds and wrong for a fraction:

    0.995 -> "1.0"    the contract says 99.5%
    0.75  -> "0.8"    the contract says 75%

The second is the one that would land badly. It is FIVE POINTS PERMISSIVE on the
memory ceiling, in the document whose entire purpose is to show the ceiling was
held, and the JSON payload beside it carries the correct 0.75, so the two
exported artifacts contradict each other.

Every expected string here is derived FROM the constant in ``gates`` rather than
typed in. A test that hardcodes "99.5%" passes forever after somebody changes
the constant and forgets the renderer, which is the failure it exists to catch.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import gates, run_report
from voicegateway.livekit_diag.run_report import _gate_number, _ratio_pct


def _pct(value: float) -> str:
    """The percentage a reader should see, derived rather than written out."""
    return _ratio_pct(value)


# --------------------------------------------------------------------------
# The two that were wrong
# --------------------------------------------------------------------------


def test_the_establishment_floor_is_not_rounded_to_one() -> None:
    rendered = _gate_number(gates.MIN_ESTABLISHMENT_RATIO, gates.ESTABLISHMENT_GATE)
    assert rendered == _pct(gates.MIN_ESTABLISHMENT_RATIO)
    assert rendered == "99.5%"
    assert rendered != "1.0"


def test_the_memory_ceiling_is_not_rounded_up_five_points() -> None:
    """The permissive one. "0.8" claims a ceiling the contract never granted."""
    rendered = _gate_number(gates.MAX_NODE_MEMORY_UTILISATION, gates.NODE_MEMORY_GATE)
    assert rendered == _pct(gates.MAX_NODE_MEMORY_UTILISATION)
    assert rendered == "75%"
    assert rendered != "0.8"


@pytest.mark.parametrize(
    ("constant", "gate"),
    [
        (gates.MIN_ESTABLISHMENT_RATIO, gates.ESTABLISHMENT_GATE),
        (gates.MAX_NODE_CPU_UTILISATION, gates.NODE_CPU_GATE),
        (gates.MAX_NODE_MEMORY_UTILISATION, gates.NODE_MEMORY_GATE),
        (gates.MIN_HEADROOM_FRACTION, gates.HEADROOM_GATE),
    ],
)
def test_every_contracted_threshold_renders_without_loss(constant, gate) -> None:
    """The property, stated once: rendering must not change the number.

    Parsed back out of the string and compared to the constant, so it holds for
    thresholds nobody has thought of yet rather than only for today's four.
    """
    rendered = _gate_number(constant, gate)
    assert rendered.endswith("%")
    assert float(rendered.rstrip("%")) / 100 == pytest.approx(constant, abs=1e-9)


# --------------------------------------------------------------------------
# The trap: not every gate carries a ratio
# --------------------------------------------------------------------------


def test_milliseconds_do_not_acquire_a_percent_sign() -> None:
    """A percent sign on a latency would be a new defect replacing an old one."""
    for gate in gates.ALL_GATES - gates.RATIO_GATES:
        rendered = _gate_number(1500.0, gate)
        assert "%" not in rendered, gate
        assert rendered == "1,500.0"


def test_every_gate_is_classified() -> None:
    """A new gate must be sorted into one bucket rather than defaulting.

    Falling through to the numeric branch is what produced this defect: the
    ratio gates were never distinguished from the millisecond ones at all.
    """
    assert gates.RATIO_GATES <= gates.ALL_GATES
    declared = {
        value
        for name, value in vars(gates).items()
        if name.endswith("_GATE") and isinstance(value, str)
    }
    assert declared == gates.ALL_GATES, declared ^ gates.ALL_GATES


def test_an_unmeasured_value_is_still_not_measured() -> None:
    """None must not become 0%, on either kind of gate."""
    for gate in gates.ALL_GATES:
        assert "not measured" in _gate_number(None, gate)
        assert "0%" not in _gate_number(None, gate)


def test_the_classification_is_not_inferred_from_the_metric_name() -> None:
    """The reason RATIO_GATES exists rather than a suffix rule.

    An unmeasured headroom gate carries no metric and a real 0.2 threshold. A
    name-based rule would see None and render the contracted 20% as "0.2".
    """
    [unmeasured] = gates.headroom_gates(
        [
            gates.HeadroomReading(
                node="n",
                resource=gates.HEADROOM_RTP_PORTS,
                used=None,
                limit=None,
                unmeasured_reason="nothing scrapes it",
            )
        ]
    )
    assert unmeasured.metric is None
    assert unmeasured.threshold == gates.MIN_HEADROOM_FRACTION
    assert _gate_number(unmeasured.threshold, unmeasured.gate) == _pct(
        gates.MIN_HEADROOM_FRACTION
    )


# --------------------------------------------------------------------------
# Through the renderer, not only the helper
# --------------------------------------------------------------------------


def _html_with(gate_results) -> str:
    return run_report.render_load_html(
        run_report.build_load_payload(
            run={"id": "r", "artifact_sha256": "a" * 64},
            tests=[],
            gate_results=[g.as_dict() for g in gate_results],
        )
    )


def test_the_rendered_html_states_the_contracted_thresholds() -> None:
    """End to end, against the strings the constants produce."""
    html = _html_with(
        [
            gates.establishment_gate(
                attempted=15000,
                succeeded=14985,
                threshold=gates.MIN_ESTABLISHMENT_RATIO,
                subject="ramp-500",
            ),
            *gates.node_memory_gates(
                [
                    gates.NodeUtilisationReading(
                        node="sfu-1", utilisation=0.61, samples=12
                    )
                ]
            ),
        ]
    )
    assert _pct(gates.MIN_ESTABLISHMENT_RATIO) in html
    assert _pct(gates.MAX_NODE_MEMORY_UTILISATION) in html
    # And the shapes that used to appear in their place are gone.
    assert ">1.0<" not in html
    assert ">0.8<" not in html


def test_the_html_and_the_payload_agree(tmp_path) -> None:
    """The two exported artifacts must not contradict each other.

    The JSON is the correct one and is not touched; this asserts the HTML now
    says the same thing rather than the JSON being bent to match.
    """
    results = gates.node_memory_gates(
        [gates.NodeUtilisationReading(node="sfu-1", utilisation=0.61, samples=12)]
    )
    payload = run_report.build_load_payload(
        run={"id": "r", "artifact_sha256": "a" * 64},
        tests=[],
        gate_results=[g.as_dict() for g in results],
    )
    [gate] = payload["gates"]
    assert gate["threshold"] == gates.MAX_NODE_MEMORY_UTILISATION
    html = run_report.render_load_html(payload)
    assert _pct(gate["threshold"]) in html
