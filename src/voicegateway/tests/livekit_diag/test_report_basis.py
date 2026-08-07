"""What the diagnostics report is based on, stated in the document.

Three facts a reader needs before they can weigh anything else, and which the
report did not say.

**How many turns.** Derivable by adding up a per-agent column, and never added
up. A reader had to do arithmetic to learn the sample size behind a percentile.

**How many sessions.** Not derivable at all from the rendered page.

**Which environment.** The LiveKit URL is close and is a different claim: it
says which server was probed, not whether the probe ran inside the deployed
environment or against it from a laptop over a different network path. Nothing
can derive that, so it is declared, and an undeclared report says so rather than
letting a reader assume either answer.
"""

from __future__ import annotations

from voicegateway.livekit_diag import run_report
from voicegateway.livekit_diag.run_report import RunRecord


def _run(agents: list[dict] | None = None) -> RunRecord:
    return RunRecord(
        run_id="r-1",
        checks=["latency"],
        config={"target_ms": 1500},
        status="done",
        results={
            "checks": {
                "latency": {
                    "ok": True,
                    "result": {"agents": agents if agents is not None else []},
                }
            }
        },
        verdict="PASS",
        created_at="2026-08-07T12:00:00+00:00",
    )


def _agent(name: str, trials: int, room: str | None) -> dict:
    return {"agent": name, "stats": {"trials": trials}, "room": room}


def _payload(run: RunRecord, environment: str | None = None) -> dict:
    return run_report.build_payload(run, livekit_url="wss://x", environment=environment)


# --------------------------------------------------------------------------
# Counts
# --------------------------------------------------------------------------


def test_turns_are_summed_across_agents_so_nobody_adds_a_column_by_hand() -> None:
    basis = _payload(
        _run([_agent("a", 3, "vg-probe-a"), _agent("b", 4, "vg-probe-b")])
    )["basis"]
    assert basis["turns"] == 7


def test_sessions_counts_distinct_probe_rooms() -> None:
    """A session here IS the room a probed agent's counted turns ran in."""
    basis = _payload(
        _run([_agent("a", 3, "vg-probe-a"), _agent("b", 4, "vg-probe-b")])
    )["basis"]
    assert basis["sessions"] == 2


def test_an_agent_that_answered_nothing_records_no_session() -> None:
    """It attempted; it produced no measurement. Counting it would inflate the
    denominator of a report whose whole job is to say what it measured."""
    basis = _payload(_run([_agent("a", 3, "vg-probe-a"), _agent("b", 0, None)]))[
        "basis"
    ]
    assert basis["sessions"] == 1
    assert basis["turns"] == 3


def test_two_agents_sharing_one_room_are_one_session() -> None:
    """Distinct rooms, not agent count: a fixed --room-name reuses one."""
    basis = _payload(_run([_agent("a", 2, "shared"), _agent("b", 2, "shared")]))[
        "basis"
    ]
    assert basis["sessions"] == 1
    assert basis["turns"] == 4


def test_a_run_that_probed_nothing_reports_zero_not_null() -> None:
    """Zero sessions is a measurement here, unlike a zero latency: the run
    genuinely produced none, and that is the finding."""
    basis = _payload(_run([]))["basis"]
    assert basis["sessions"] == 0
    assert basis["turns"] == 0


# --------------------------------------------------------------------------
# Environment: declared, never inferred
# --------------------------------------------------------------------------


def test_a_declared_environment_is_carried_and_labelled_as_declared() -> None:
    basis = _payload(_run([]), environment="acme-prod-iad")["basis"]
    assert basis["environment"] == "acme-prod-iad"
    assert basis["environment_declared"] is True
    assert "declared" in basis["environment_source"]


def test_an_undeclared_environment_is_null_and_says_so(monkeypatch) -> None:
    monkeypatch.delenv(run_report.ENVIRONMENT_ENV_VAR, raising=False)
    basis = _payload(_run([]))["basis"]
    assert basis["environment"] is None
    assert basis["environment_declared"] is False


def test_the_env_var_supplies_it_when_the_caller_does_not(monkeypatch) -> None:
    monkeypatch.setenv(run_report.ENVIRONMENT_ENV_VAR, "staging-lhr")
    assert _payload(_run([]))["basis"]["environment"] == "staging-lhr"


def test_an_explicit_argument_beats_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv(run_report.ENVIRONMENT_ENV_VAR, "from-env")
    assert _payload(_run([]), environment="explicit")["basis"]["environment"] == (
        "explicit"
    )


def test_the_livekit_url_is_not_treated_as_the_environment(monkeypatch) -> None:
    """The distinction the whole field exists for.

    A report can name the exact production server it probed and still have been
    run from a laptop across the internet, which is a different network path and
    therefore different latency.
    """
    monkeypatch.delenv(run_report.ENVIRONMENT_ENV_VAR, raising=False)
    payload = run_report.build_payload(_run([]), livekit_url="wss://prod.livekit")
    assert payload["target"]["livekit_url"] == "wss://prod.livekit"
    assert payload["basis"]["environment"] is None


# --------------------------------------------------------------------------
# The document says it, not merely the payload
# --------------------------------------------------------------------------


def test_the_rendered_report_states_both_counts(monkeypatch) -> None:
    """The contractual requirement is about the DOCUMENT a reader opens."""
    monkeypatch.delenv(run_report.ENVIRONMENT_ENV_VAR, raising=False)
    html = run_report.render_html(
        _payload(_run([_agent("a", 3, "room-a"), _agent("b", 4, "room-b")]))
    )
    assert "Measured over" in html
    assert "2 session(s), 7 turn(s)" in html


def test_the_rendered_report_names_the_declared_environment() -> None:
    html = run_report.render_html(_payload(_run([]), environment="acme-prod-iad"))
    assert "Environment" in html
    assert "acme-prod-iad" in html


def test_an_undeclared_environment_is_not_left_blank(monkeypatch) -> None:
    """A blank cell reads as a rendering fault. It has to say it is undeclared,
    and say how to declare it, or the next reader guesses."""
    monkeypatch.delenv(run_report.ENVIRONMENT_ENV_VAR, raising=False)
    html = run_report.render_html(_payload(_run([])))
    assert "not declared" in html
    assert run_report.ENVIRONMENT_ENV_VAR in html
