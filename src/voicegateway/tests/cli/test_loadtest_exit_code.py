"""`voicegw loadtest report` exiting on what the gates decided.

The command computed a verdict, printed it, and returned 0 regardless. A run
that FAILed every acceptance criterion exited exactly as cleanly as one that
passed, so any pipeline checking the status code was checking nothing.

Two properties are pinned.

**Only PASS is clean.** WAIVED is a gate nobody held the run to, and a pipeline
going green on a waiver has dropped the requirement rather than recorded it.
UNKNOWN means the run failed to measure something, which is not the same as
demonstrating it.

**The evidence is written before the exit.** A failing run is precisely the one
whose report somebody needs; exiting first would destroy the record of why.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.livekit_diag import gates

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> dict:
    """A throwaway database and an imported run, never the repo's voicegw.db."""
    monkeypatch.delenv("VOICEGW_DB_PATH", raising=False)
    fixtures = Path(__file__).resolve().parents[4] / ".agents" / "fixtures"
    if not (fixtures / "summary.json").is_file():
        pytest.skip("fixtures are not present in this checkout")

    run_dir = tmp_path / "ramp-500"
    run_dir.mkdir()
    for name in ("summary.json", "gossipper_stats.csv"):
        (run_dir / name).write_bytes((fixtures / name).read_bytes())

    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.dump(
            {"cost_tracking": {"enabled": True, "db_path": str(tmp_path / "t.db")}}
        )
    )
    imported = runner.invoke(
        app, ["loadtest", "import", str(run_dir), "--config", str(config)]
    )
    assert imported.exit_code == 0, imported.output
    return {"config": str(config), "out": tmp_path / "out", "tmp": tmp_path}


def _report(workspace: dict, *extra: str):
    return runner.invoke(
        app,
        [
            "loadtest",
            "report",
            "--acceptance",
            "ramp-500",
            "--config",
            workspace["config"],
            "--out",
            str(workspace["out"]),
            *extra,
        ],
    )


# --------------------------------------------------------------------------
# The exit code follows the verdict
# --------------------------------------------------------------------------


def test_an_unknown_verdict_exits_non_zero(workspace: dict) -> None:
    """The fixtures have no node samples, so four criteria are UNKNOWN.

    UNKNOWN is not a pass: the run failed to measure something rather than
    demonstrating it, and a pipeline must not go green on that.
    """
    result = _report(workspace)
    assert "UNKNOWN" in result.output
    assert result.exit_code == 1


def test_the_report_is_written_before_the_process_exits(workspace: dict) -> None:
    """The single most important property here.

    A failing run is exactly the run whose evidence somebody needs. Exiting
    before writing it would leave nothing to explain the failure.
    """
    result = _report(workspace)
    assert result.exit_code == 1
    written = sorted(p.name for p in workspace["out"].iterdir())
    assert any(n.endswith(".json") for n in written), written
    assert any(n.endswith(".html") for n in written), written
    # And the file actually carries the gates, not just an empty shell.
    payload = json.loads(
        next(p for p in workspace["out"].iterdir() if p.suffix == ".json").read_text()
    )
    assert payload["gates_recorded"] is True
    assert payload["gates"]


def test_only_pass_is_clean() -> None:
    """Pinned against the gates' own convention, not restated here."""
    assert gates.exit_code(gates.PASS) == 0
    for status in (gates.WAIVED, gates.WARN, gates.UNKNOWN, gates.FAIL):
        assert gates.exit_code(status) == 1, status


def test_a_waiver_does_not_buy_a_clean_exit() -> None:
    """A waived gate is one nobody held the run to.

    Called out separately from the loop above because it is the one people
    expect to be clean, and a pipeline that goes green on it has silently
    dropped the requirement rather than recording that it was set aside.
    """
    assert gates.exit_code(gates.WAIVED) != 0


def test_the_cli_uses_the_gates_convention_rather_than_its_own() -> None:
    import inspect

    from voicegateway.cli import loadtest_cli

    source = inspect.getsource(loadtest_cli)
    assert "exit_code(run_verdict)" in source
    # The gates deliberately use ONE non-zero code so an existing
    # `if [ $? -eq 1 ]` keeps catching every kind of failure. A second
    # convention here that split WARN and FAIL across 2 and 3 would silently
    # stop such a pipeline noticing two thirds of them.
    for status in (gates.WARN, gates.UNKNOWN, gates.FAIL, gates.WAIVED):
        assert gates.exit_code(status) == 1


def test_a_dry_run_import_still_exits_zero(workspace: dict) -> None:
    """Non-vacuous: the exit is driven by the verdict, not unconditional."""
    fixtures_dir = workspace["tmp"] / "ramp-500"
    result = runner.invoke(
        app,
        [
            "loadtest",
            "import",
            str(fixtures_dir),
            "--config",
            workspace["config"],
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
