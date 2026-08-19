"""A comparison that cannot be made must not read as a pass.

Latency and cost regressions are the ones that ship, because nothing fails when
they do. A test suite catches an agent that is broken; nothing caught an agent
that got 300ms slower or 40% more expensive per call, and the caller feels the
first while the bill shows the second.

Every test here is the same rule seen from a different side. The dangerous
failure is not a missed regression, it is a green exit code on a window that
never supported the comparison, because that is indistinguishable from a real
pass to the CI job reading it.
"""

from __future__ import annotations

import pytest

from voicegateway.services.baseline_service import (
    EXIT_DRIFT,
    EXIT_INSUFFICIENT,
    EXIT_OK,
    compare,
)


def _window(**metrics) -> dict:
    """A collected window. Each metric carries the count it was taken over."""
    return {
        "version": 1,
        "window": {"period": "week", "project": None},
        "metrics": {
            name: {"value": value, "samples": samples}
            for name, (value, samples) in metrics.items()
        },
    }


# --------------------------------------------------------------------------
# Round trip and the happy path
# --------------------------------------------------------------------------


def test_an_unchanged_window_passes() -> None:
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(800.0, 200)))
    assert report.exit_code == EXIT_OK


def test_movement_inside_tolerance_is_not_drift() -> None:
    """5% on a metric allowed 20%. A gate that fires on noise gets disabled."""
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(840.0, 200)))
    assert report.exit_code == EXIT_OK


# --------------------------------------------------------------------------
# Drift, named and quantified
# --------------------------------------------------------------------------


def test_a_regression_fails_and_names_the_metric_and_the_delta() -> None:
    """A bare non-zero exit sends somebody back to the dashboard to work out
    what happened, which is the work this command exists to remove."""
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(1200.0, 200)))
    assert report.exit_code == EXIT_DRIFT
    line = next(f.describe() for f in report.findings if f.status == "drift")
    assert "response_speed_p95_ms" in line
    assert "800" in line and "1200" in line
    assert "50.0%" in line


def test_tolerance_is_per_metric_not_global() -> None:
    """Cost and p95 do not move for the same reasons.

    12% is inside p95's 20% and outside cost's 10%. One global number would have
    to be wrong for one of them, and the cost side is the one that quietly bills.
    """
    pinned = _window(response_speed_p95_ms=(800.0, 200), cost_per_call_usd=(0.10, 200))
    report = compare(
        pinned,
        _window(response_speed_p95_ms=(896.0, 200), cost_per_call_usd=(0.112, 200)),
    )
    by_metric = {f.metric: f.status for f in report.findings}
    assert by_metric["response_speed_p95_ms"] == "ok"
    assert by_metric["cost_per_call_usd"] == "drift"


def test_an_improvement_beyond_tolerance_is_still_reported() -> None:
    """A 50% drop is either very good news or a measurement that broke.

    Both are worth a human look, and silently passing the second is how a
    collector that stopped recording reads as a performance win.
    """
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(400.0, 200)))
    assert report.exit_code == EXIT_DRIFT


# --------------------------------------------------------------------------
# The cases that must not read as a pass
# --------------------------------------------------------------------------


def test_too_few_samples_reports_insufficient_rather_than_passing() -> None:
    """Three calls against a baseline of two hundred is not a result."""
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(805.0, 3)))
    assert report.exit_code == EXIT_INSUFFICIENT
    assert "INSUFFICIENT DATA" in report.findings[0].describe()


def test_a_metric_missing_from_the_new_window_is_named_not_passed() -> None:
    """This is the case where something quietly stopped being collected."""
    pinned = _window(response_speed_p95_ms=(800.0, 200), cost_per_call_usd=(0.10, 200))
    report = compare(pinned, _window(cost_per_call_usd=(0.10, 200)))
    assert report.exit_code == EXIT_INSUFFICIENT
    missing = next(f for f in report.findings if f.status == "unmeasured")
    assert missing.metric == "response_speed_p95_ms"
    assert "UNMEASURED" in missing.describe()


def test_a_null_value_counts_as_unmeasured_not_as_zero() -> None:
    """Absent is not zero, the same rule the turn rows follow."""
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(pinned, _window(response_speed_p95_ms=(None, 200)))
    assert report.exit_code == EXIT_INSUFFICIENT


def test_a_pinned_zero_is_reported_rather_than_divided_by() -> None:
    """Everything is an infinite increase from zero, which is not a ratio."""
    pinned = _window(cost_per_call_usd=(0.0, 200))
    report = compare(pinned, _window(cost_per_call_usd=(0.05, 200)))
    assert report.exit_code == EXIT_INSUFFICIENT


def test_drift_outranks_insufficiency() -> None:
    """A measured regression is the worse news and must not be buried.

    Reporting exit 2 while a metric that DID have enough samples regressed would
    hide the finding the command exists to surface.
    """
    pinned = _window(response_speed_p95_ms=(800.0, 200), cost_per_call_usd=(0.10, 200))
    report = compare(
        pinned,
        _window(response_speed_p95_ms=(1600.0, 200), cost_per_call_usd=(0.10, 2)),
    )
    assert report.exit_code == EXIT_DRIFT


def test_a_metric_the_new_window_grew_is_not_a_failure() -> None:
    """Only pinned metrics are compared.

    Otherwise every measurement added to VoiceGateway would break every existing
    baseline in CI on upgrade, which trains people to delete the gate.
    """
    pinned = _window(response_speed_p95_ms=(800.0, 200))
    report = compare(
        pinned,
        _window(response_speed_p95_ms=(800.0, 200), brand_new_metric=(42.0, 200)),
    )
    assert report.exit_code == EXIT_OK
    assert [f.metric for f in report.findings] == ["response_speed_p95_ms"]


# --------------------------------------------------------------------------
# The commands themselves, end to end
# --------------------------------------------------------------------------


def test_pin_and_check_round_trip(tmp_path) -> None:
    """A pinned baseline round-trips, and check re-reads the same window.

    Goes through the real CLI rather than the compare() above, because the file
    format is the contract between two invocations that may be weeks and one
    upgrade apart.
    """
    import json

    import yaml
    from typer.testing import CliRunner

    from voicegateway.cli._app import app

    runner = CliRunner()
    db = tmp_path / "b.db"
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump({"cost_tracking": {"enabled": True, "db_path": str(db)}})
    )
    out = tmp_path / "baseline.json"

    pinned = runner.invoke(
        app,
        [
            "baseline",
            "pin",
            "--config",
            str(config),
            "--out",
            str(out),
            "--period",
            "all",
        ],
    )
    assert pinned.exit_code == 0, pinned.output
    payload = json.loads(out.read_text())
    assert payload["version"] == 2
    # The window is recorded, so a baseline is reproducible rather than a
    # snapshot of an unnamed moment.
    assert payload["window"]["period"] == "all"
    # The SOURCE travels with the window: a local-pinned baseline checked
    # against a collector is not a comparison, and check refuses on a mismatch.
    assert payload["source"]["kind"] == "local"
    assert "response_speed_p95_ms" in payload["metrics"]

    checked = runner.invoke(
        app, ["baseline", "check", "--config", str(config), "--baseline", str(out)]
    )
    # An empty database measures nothing, and "nothing to compare" must not be
    # a pass. That is the whole point.
    assert checked.exit_code == EXIT_INSUFFICIENT, checked.output


def test_a_baseline_from_another_version_is_refused(tmp_path) -> None:
    """Comparing against fields that may have moved is worse than not comparing."""
    import json

    import yaml
    from typer.testing import CliRunner

    from voicegateway.cli._app import app

    runner = CliRunner()
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {"cost_tracking": {"enabled": True, "db_path": str(tmp_path / "x.db")}}
        )
    )
    stale = tmp_path / "old.json"
    stale.write_text(json.dumps({"version": 999, "window": {}, "metrics": {}}))

    result = runner.invoke(
        app, ["baseline", "check", "--config", str(config), "--baseline", str(stale)]
    )
    assert result.exit_code == 2
    assert "999" in result.output


# --------------------------------------------------------------------------
# The source is part of the claim, and a mismatch is refused
# --------------------------------------------------------------------------


def _sourced(kind: str, base_url: str | None, project: str | None) -> dict:
    return {
        "version": 2,
        "window": {"period": "week", "project": project},
        "source": {"kind": kind, "base_url": base_url, "project": project},
        "metrics": {},
    }


def test_a_local_baseline_checked_against_a_collector_is_refused() -> None:
    """The failure that looks most like success.

    It produces numbers, a verdict and a green tick while being a comparison
    that never happened. Warned, that line is read once and then never again;
    the whole value of a pinned baseline is that a comparison either happened
    or it did not.
    """
    from voicegateway.services.baseline_service import (
        SourceMismatch,
        check_source_matches,
    )

    with pytest.raises(SourceMismatch) as excinfo:
        check_source_matches(
            _sourced("local", None, "default"),
            _sourced("collector", "https://collector.example", "default"),
        )
    assert "not a comparison" in str(excinfo.value)


def test_the_same_collector_with_a_different_project_is_also_refused() -> None:
    """A collector aggregates many agents, so the project IS the population.

    Same URL, different scope, is a different set of traffic, and comparing
    across it silently answers a question nobody asked.
    """
    from voicegateway.services.baseline_service import (
        SourceMismatch,
        check_source_matches,
    )

    url = "https://collector.example"
    with pytest.raises(SourceMismatch):
        check_source_matches(
            _sourced("collector", url, "checkout"),
            _sourced("collector", url, "support"),
        )


def test_a_matching_source_passes() -> None:
    """Non-vacuous: the guard must not refuse everything."""
    from voicegateway.services.baseline_service import check_source_matches

    same = _sourced("collector", "https://c.example", "default")
    check_source_matches(same, dict(same))


def test_unreadable_and_insufficient_are_different_exit_codes() -> None:
    """ "I could not look" is a fact about the plumbing; "one metric had nothing
    behind it" is a fact about the data. They want different responses from
    whoever is on call, and 2 already carries the second meaning."""
    from voicegateway.services.baseline_service import (
        EXIT_INSUFFICIENT,
        EXIT_SOURCE_MISMATCH,
        EXIT_UNREADABLE,
    )

    assert len({EXIT_INSUFFICIENT, EXIT_UNREADABLE, EXIT_SOURCE_MISMATCH}) == 3
