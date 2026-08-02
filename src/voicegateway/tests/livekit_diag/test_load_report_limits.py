"""The load report's limits list, which belonged to a different product.

The load report built its not-measured section as the probe's list plus its own,
so a client reading a SIP load-test report was told about the prober's data
channel, billed trial calls per agent, an agents check and the host that ran the
probe. None of those exists in a load run. The reasonable conclusion for a
reader is that they were sent output from a different tool, which costs more
credibility than the numbers buy.

The fix is not to empty the section. **The limits list is load-bearing honesty,
not boilerplate**, and dropping an entry that IS true of a load run converts an
honest disclosure into a silent omission, which is worse than the original
defect. So each probe entry was judged on its merits:

* packet loss: TRUE of a load run, and already stated in the load list with the
  reason that actually applies. Kept there, dropped here, so it appears once.
* SFU round-trip time through the prober's data channel: does not exist.
* reply latency over billed trial calls: does not exist. The load report renders
  no percentile, so the "max of N" rule it carries has nothing to qualify.
* an idle agent invisible to the agents check: there is no agents check.
* one vantage point: TRUE, and restated in load terms. Every call came from the
  host running the generator.
"""

from __future__ import annotations

import pytest

from voicegateway.livekit_diag import run_report

# Phrases that can only be true of the diagnostics probe.
#
# Compared against the HTML through _esc, the SAME escaping the renderer uses.
# Searching the raw text for "the prober's own message" would never match, since
# the apostrophe is escaped on the way in, so the assertion would pass whether
# or not the phrase was there. A vacuous test here is worse than none: it is a
# green tick over the exact defect it names.
PROBE_ONLY = [
    "the prober's own message",
    "SFU data channel",
    "billed trial calls",
    "agents check",
    "the host that ran the probe",
]


@pytest.fixture(scope="module")
def load_html() -> str:
    payload = run_report.build_load_payload(
        run={"id": "ramp-500", "artifact_sha256": "a" * 64}, tests=[]
    )
    return run_report.render_load_html(payload)


@pytest.fixture(scope="module")
def load_limits() -> list[str]:
    payload = run_report.build_load_payload(
        run={"id": "ramp-500", "artifact_sha256": "a" * 64}, tests=[]
    )
    return payload["not_measured"]


# --------------------------------------------------------------------------
# The other product's limitations are gone
# --------------------------------------------------------------------------


def test_the_escaping_matters_here() -> None:
    """Guards the guard: at least one phrase really does change under _esc.

    If every phrase were escape-identical the comparison above would look
    careful and be doing nothing.
    """
    assert any(run_report._esc(p) != p for p in PROBE_ONLY)


@pytest.mark.parametrize("phrase", PROBE_ONLY)
def test_no_probe_only_phrase_reaches_the_load_report(load_html, phrase) -> None:
    assert run_report._esc(phrase) not in load_html


def test_the_probe_list_is_not_included_wholesale(load_limits) -> None:
    """Checked against the real list, so a new probe entry cannot leak in."""
    assert not set(run_report._REPORT_LIMITS) & set(load_limits)


def test_the_preamble_does_not_call_this_a_diagnostics_run(load_html) -> None:
    """The first hint a reader gets that they were sent the wrong document."""
    assert "A diagnostics run" not in load_html
    assert "load run" in load_html


# --------------------------------------------------------------------------
# ...and the diagnostics report is untouched, where they are correct
# --------------------------------------------------------------------------


def test_the_diagnostics_report_still_carries_its_own_limits() -> None:
    """The trap. These entries are right there and must not be collateral."""
    payload = run_report.build_payload(
        run_report.RunRecord(
            run_id="d1", checks=["agents"], config={}, status="done", created_at="t"
        ),
        livekit_url=None,
    )
    assert payload["not_measured"] == list(run_report._REPORT_LIMITS)
    html = run_report.render_html(payload)
    for phrase in PROBE_ONLY:
        assert run_report._esc(phrase) in html, phrase
    assert "A diagnostics run" in html


# --------------------------------------------------------------------------
# Nothing genuinely unmeasured went quiet
# --------------------------------------------------------------------------


def test_every_structurally_unmeasured_surface_is_still_named(load_limits) -> None:
    """Removing a true entry would be worse than the defect being fixed.

    Named by the thing each one is about, so a rewording does not fail this but
    a deletion does.
    """
    joined = " ".join(load_limits).lower()
    for subject in (
        "rtp-port headroom",
        "network headroom",
        "packet loss",
        "time window overlap",
        "worst node",
        "calls-per-node",
        "one vantage point",
    ):
        assert subject in joined, subject


def test_packet_loss_is_named_once_with_the_reason_that_applies(
    load_limits,
) -> None:
    """It was in both lists, with two different reasons, one of them a probe's."""
    mentions = [item for item in load_limits if "packet loss" in item.lower()]
    assert len(mentions) == 1
    assert "server-side surface" in mentions[0]
    assert "probe" not in mentions[0].lower()


def test_the_vantage_limitation_survived_in_load_terms(load_limits) -> None:
    """True of a load run, so it had to stay; false as written, so it changed."""
    [vantage] = [item for item in load_limits if "vantage point" in item]
    assert "generator" in vantage
    assert "probe" not in vantage.lower()


def test_the_list_did_not_shrink_to_nothing(load_limits) -> None:
    """A limits section that goes quiet reads as a clean bill of health."""
    assert len(load_limits) >= len(run_report._LOAD_REPORT_LIMITS)
    assert len(load_limits) >= 7
