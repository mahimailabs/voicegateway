"""A missing DENOMINATOR is not a missing SERIES, and the difference is an hour.

Both read UNKNOWN and the two remedies point opposite ways. A series nobody
scraped needs the fleet run again. A node nobody declared a network baseline for
needs two lines of JSON and a re-import of the artifacts already on disk.

Filed as one bucket, the report told an operator holding 257 good node_exporter
samples per node to "re-run against the current scrape configuration". That is a
500-concurrent fleet hour that could not have changed the outcome, and the gate's
own detail line sitting beside it said the true thing the whole time: the node
carried throughput and nothing declared a baseline for it. Thirty-two gates on
one real run were UNKNOWN because a SIP tier scaled to 4 while the baseline file
still stopped at sip-2.

So there are three defences here and they are deliberately at three different
depths:

* the CLASSIFIER stops sending the operator to the wrong remedy (after the fact),
* the IMPORT WARNING names the undeclared nodes while they can still be fixed
  cheaply (before the report is ever read), and
* the baseline file may now carry its own documentation, so the copy in a repo
  and the copy on the collector can be the same bytes. They drifted, and that
  drift is what let the omission survive to the report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.cli.loadtest_cli import (
    _network_baselines_from_file,
    _plan_from_file,
)
from voicegateway.livekit_diag import run_report
from voicegateway.repository import node_samples_repository as node_samples
from voicegateway.services.storage_service import StorageService

runner = CliRunner()

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "loadtest"
ACCEPTANCE = FIXTURES / "acceptance-500"

NODE = "sfu-1"
SOURCE = "node-exporter"
WINDOW = (1_785_661_201_000, 1_785_661_320_000)
COUNTER_ORIGIN_S = 1_785_661_200
RX_BYTES_PER_SECOND = 50_000_000.0
TX_BYTES_PER_SECOND = 60_000_000.0
DECLARED_BASELINE_BPS = 3_125_000_000.0
CORES = 4.0
TOTAL_BYTES = 16 * 2**30


# --------------------------------------------------------------------------
# The classifier: which remedy a gap is sent to
# --------------------------------------------------------------------------


def _uncollected(detail: str, subject: str = "sip-3/network_in"):
    """One not-collected entry, built from the detail a gate actually recorded.

    ``value`` is None because that is what puts a gate in the not-collected list
    at all: the classification under test reads only the prose beside it.
    """
    entries = run_report.not_collected_entries(
        [
            {
                "gate": "resource_headroom",
                "subject": subject,
                "status": "unknown",
                "value": None,
                "detail": detail,
            }
        ]
    )
    assert entries is not None
    return entries[0]


#: Verbatim from ``_bandwidth_gates_for``. Copied rather than imported so that
#: rewording the gate's prose without rewording the marker fails HERE, loudly,
#: instead of silently routing the row back to the re-capture remedy.
NO_BASELINE_DETAIL = (
    "sip-3 carried throughput but no inbound baseline was declared for it, and "
    "there is nothing to measure link capacity against. The instance type is "
    "deliberately not read to infer one"
)


def test_a_missing_denominator_is_not_filed_as_a_missing_series() -> None:
    """The bug: 32 gates told an operator to re-run a scrape that was complete."""
    entry = _uncollected(NO_BASELINE_DETAIL)
    assert "not in this run's scrape" not in entry["cause"]
    assert "denominator" in entry["cause"]


def test_the_remedy_says_re_import_and_never_re_capture() -> None:
    """The whole cost of the old wording was one wrong verb.

    Asserted as a positive AND a negative: a remedy that adds "re-import" while
    leaving "re-run the scrape" in place has not fixed anything for a reader who
    stops at the first instruction.
    """
    change = _uncollected(NO_BASELINE_DETAIL)["change"]
    assert "--network-baseline" in change
    assert "RE-IMPORT THE SAME ARTIFACTS" in change
    assert "Re-run against" not in change


def test_the_node_that_wants_a_baseline_is_still_named_verbatim() -> None:
    """The shared remedy cannot name a node, so the per-row detail must."""
    assert "sip-3" in _uncollected(NO_BASELINE_DETAIL)["recorded"]


def test_a_genuinely_unscraped_series_still_routes_to_a_re_run() -> None:
    """The guard on the fix. The old remedy is right for the case it was written
    for, and narrowing it must not empty it."""
    entry = _uncollected("nothing in this run's scrape carried the series it needs")
    assert "not in this run's scrape" in entry["cause"]
    assert "Re-run against" in entry["change"]


def test_a_baseline_that_was_never_ESTABLISHED_is_not_a_missing_denominator() -> None:
    """Two different absent baselines, one word apart in the prose.

    ``return_to_baseline`` says "no baseline was ESTABLISHED" when no idle sample
    exists outside the window, and that one really does need another run. The
    marker keys on "declared", so this must not be captured by it.
    """
    entry = _uncollected(
        "not measured: no sample outside the test window carried this resource, "
        "so no baseline was established and nothing shows it came back"
    )
    assert "denominator" not in entry["cause"]


# --------------------------------------------------------------------------
# The import warning: named while it is still cheap to fix
# --------------------------------------------------------------------------


async def _seed(db_path: Path, *, throughput: bool) -> None:
    """One node's window, with or without the network byte counters.

    CPU and memory are seeded either way, so the node is in the scrape and
    correlates in both variants. That is what makes the idle case below a real
    assertion rather than a test of an empty database: the node is present and
    measured, it simply moved no traffic.

    The throughput counters are cumulative and have to climb in ABSOLUTE time
    rather than restart per window, because a bandwidth peak is read as a rate
    and a counter that goes backwards is a reset rather than a number.
    """
    storage = StorageService(db_path=str(db_path))
    await storage._ensure_initialized()
    start_ms, end_ms = WINDOW
    idle_rate = CORES * (1.0 - 0.4)
    available = int(TOTAL_BYTES * 0.6)
    async with storage._conn.session() as db:
        rows = []
        for index in range(0, (end_ms - start_ms) // 10_000 + 1):
            at_ms = start_ms + index * 10_000
            elapsed = index * 10
            since_origin = at_ms // 1000 - COUNTER_ORIGIN_S
            values = {
                "cpu_seconds_total": 1000.0 + CORES * elapsed,
                "cpu_idle_seconds_total": 800.0 + idle_rate * elapsed,
                "memory_total_bytes": float(TOTAL_BYTES),
                "memory_available_bytes": float(available),
            }
            if throughput:
                values["network_receive_bytes_total"] = (
                    RX_BYTES_PER_SECOND * since_origin
                )
                values["network_transmit_bytes_total"] = (
                    TX_BYTES_PER_SECOND * since_origin
                )
            rows.append(
                node_samples.NodeSampleInput(
                    node=NODE,
                    source=SOURCE,
                    at_ms=at_ms,
                    outcome="ok",
                    values=values,
                )
            )
        await node_samples.insert_samples(db, rows)
    await storage.aclose()


def _import(tmp_path: Path, *, declare: bool, throughput: bool = True):
    """Seed a scrape, then import with or without a declared baseline."""
    import asyncio

    db = tmp_path / "throwaway.db"
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump({"cost_tracking": {"enabled": True, "db_path": str(db)}})
    )
    # Before the import: correlation happens at import time, so a sample written
    # afterwards would never reach the row.
    asyncio.run(_seed(db, throughput=throughput))

    argv = [
        "loadtest",
        "import",
        str(ACCEPTANCE),
        "--captured",
        "--config",
        str(config),
    ]
    if declare:
        baseline = tmp_path / "network-baseline.json"
        baseline.write_text(
            json.dumps(
                {
                    NODE: {
                        "in_bps": DECLARED_BASELINE_BPS,
                        "out_bps": DECLARED_BASELINE_BPS,
                    }
                }
            )
        )
        argv += ["--network-baseline", str(baseline)]
    return runner.invoke(app, argv)


def test_import_names_the_scraped_node_that_has_no_declared_baseline(
    tmp_path: Path,
) -> None:
    """The class fix. Import is the only place both lists are in one hand.

    The baseline file is written by an operator and the node names come out of
    the scrape: nothing earlier can compare them and nothing later still holds
    the file. Unsaid here, a tier that scaled after the file was written becomes
    UNKNOWN headroom rows in a report read weeks later.
    """
    result = _import(tmp_path, declare=False)
    assert result.exit_code == 0, result.output
    assert NODE in result.output
    assert "no declared baseline" in result.output
    assert "UNKNOWN" in result.output


def test_the_warning_points_at_a_re_import_not_a_re_capture(tmp_path: Path) -> None:
    """Same wrong verb, caught at the other end of the pipeline."""
    output = _import(tmp_path, declare=False).output
    assert "re-import" in output
    assert "Nothing needs re-capturing" in output


def test_a_declared_node_is_not_warned_about(tmp_path: Path) -> None:
    """The warning has to go quiet once it is satisfied, or it trains an
    operator to scroll past it."""
    result = _import(tmp_path, declare=True)
    assert result.exit_code == 0, result.output
    assert "no declared baseline" not in result.output


def test_an_idle_scraped_node_with_no_baseline_is_not_warned_about(
    tmp_path: Path,
) -> None:
    """A WARNING THAT FIRES ON NON-PROBLEMS IS ONE PEOPLE LEARN TO SKIP.

    This is the case with nothing else asserting it, and it sits between the two
    that do: throughput with no baseline warns, throughput with a baseline stays
    silent. Here the node is scraped, correlates, and carries no throughput at
    all, and it has no baseline either. It must still produce NO warning.

    The reason is not that the node is uninteresting. It is that a node with no
    measured rate produces no `bandwidth_peaks` entry, so the bandwidth gate
    emits no headroom row for it, so there is no UNKNOWN for a warning to be
    warning ABOUT. Warning here would be noise with no corresponding defect.

    This is exactly what a well-meaning change would break. Sourcing the warning
    from the scrape TARGET LIST instead of from `bandwidth_peaks` reads as the
    more thorough option and would fire on every idle node in the fleet. That is
    the failure this test exists to catch, and it is invisible from the
    assertion alone, which is why it is written down here.
    """
    result = _import(tmp_path, declare=False, throughput=False)
    assert result.exit_code == 0, result.output
    # The node really was scraped and correlated. Without this the test would
    # pass just as well against an empty database, proving nothing.
    assert "node samples overlap" in result.output
    assert "across 1 targets" in result.output
    # And no warning, despite having no baseline entry. The node's NAME is
    # absent from the output entirely, which is the tell: this command prints a
    # node name only when it is warning about one.
    assert "no declared baseline" not in result.output
    assert NODE not in result.output


# --------------------------------------------------------------------------
# The file may document itself
# --------------------------------------------------------------------------


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "declaration.json"
    path.write_text(json.dumps(payload))
    return path


def test_a_baseline_file_may_carry_a_comment(tmp_path: Path) -> None:
    """JSON has no comment syntax, so the convention has to come from the reader.

    Without this the documented copy in a repo could not be loaded, so a stripped
    duplicate was hand-placed on the collector and the two drifted with nothing
    comparing them. That drift is how a whole SIP tier's baseline went missing.
    """
    path = _write(
        tmp_path,
        {
            "_comment": ["published c7i.2xlarge figure, AWS docs, checked 2026-08"],
            "sip-1": {"in_bps": 3_125_000_000, "out_bps": 3_125_000_000},
        },
    )
    assert _network_baselines_from_file(path) == {
        "sip-1": {"in_bps": 3_125_000_000.0, "out_bps": 3_125_000_000.0}
    }


def test_a_plan_file_may_carry_a_comment_too(tmp_path: Path) -> None:
    """The sibling file, fixed with it. A convention that works in one of two
    hand-written files beside each other is a trap rather than a convention."""
    path = _write(tmp_path, {"_comment": "from the scenario file", "ramp-100": 100})
    assert _plan_from_file(path) == {"ramp-100": {"target_concurrency": 100}}


def test_a_misspelled_field_is_still_refused(tmp_path: Path) -> None:
    """Skipping ``_`` keys must not become skipping validation.

    A node is not a comment, and a declaration that looks complete and is not is
    the exact failure this whole file exists about.
    """
    path = _write(tmp_path, {"sip-1": {"in_bps": 1, "out_bsp": 1}})
    with pytest.raises(typer.BadParameter) as excinfo:
        _network_baselines_from_file(path)
    assert "out_bsp" in str(excinfo.value)
