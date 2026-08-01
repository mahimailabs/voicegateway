"""Turn a directory of load-generator artifacts into rows this system stores.

Planning is separated from writing on purpose. Everything here is pure: it reads
files and returns an :class:`ImportPlan`, and the CLI is what talks to storage.
That is what makes the mapping testable without a database, and it is what lets
``--dry-run`` show exactly what would be written rather than a description of it.

Provenance under-claims
-----------------------

A run is recorded as SYNTHETIC unless the operator explicitly declares the
artifacts were captured from a real run. ``load_runs.has_artifact`` is derived
from ``artifact_sha256`` being present, so withholding the checksum on a
synthetic import is what makes every downstream report stamp itself synthetic.

The checksum is still computed either way and recorded in ``notes``, so an
operator can always tell which bytes a row came from. Only its promotion to
``artifact_sha256`` is gated. Defaulting this way means a forgotten flag
produces an under-claim, never a report that calls fixture numbers measured.

Per-call observations
---------------------

``POST /v1/calls/observations`` takes one call at a time and needs a
``room_sid`` or ``attempt_id`` to correlate on. These artifacts carry neither:
``summary.json`` and the stat CSV are aggregates, and the per-call file's record
schema is documented nowhere. So :func:`observations_for` returns what the
artifacts actually support, which today is nothing.

Deriving 15,000 observations from a 15,000-call total would invent calls nobody
observed, each with a fabricated identifier, and they would then be
indistinguishable from real ones in every surface downstream. The aggregate
columns on ``load_run_tests`` already carry what these artifacts measured.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from voicegateway.loadtest.artifacts import (
    CALL_RECORDS_FILENAME,
    SUMMARY_FILENAME,
    MissingArtifact,
    ParsedTest,
    _find_stat_file,
    parse_test_directory,
)
from voicegateway.repository.load_runs_repository import (
    DEFAULT_PROJECT,
    LoadRunInput,
    LoadRunTestInput,
)

# Files whose bytes go into a run's checksum, BY NAME. The call-record file is
# included even though nothing interprets it: the checksum answers "which
# artifacts was this row built from", and a run that shipped one is not the same
# run as a run that did not.
#
# The stat file is NOT here, because it cannot be recognised by extension: the
# generator names it gossipper_<pid>_stats.log. It is added by content in
# :func:`_artifact_files`. Leaving it to this tuple checksummed a run's
# calls.jsonl and silently omitted the file every number in the row came from,
# so two runs with the same call records and different measurements hashed
# identically.
_CHECKSUMMED_SUFFIXES = (".json", ".csv", ".jsonl")


@dataclass(frozen=True)
class ImportPlan:
    """Everything one import would write, before anything is written."""

    run: LoadRunInput
    tests: list[LoadRunTestInput]
    parsed: list[ParsedTest]
    # Human-readable statements about what these artifacts could NOT supply.
    # Surfaced by the CLI so a missing column is visible at import time rather
    # than as a blank cell in a report weeks later.
    limitations: list[str] = field(default_factory=list)

    @property
    def is_synthetic(self) -> bool:
        """Whether every report built from this run must stamp itself synthetic."""
        return not self.run.artifact_sha256


def _artifact_files(directory: Path) -> list[Path]:
    """The artifact files in one directory, in a stable order.

    Two rules, because the artifacts answer to two different kinds of name. The
    fixed surfaces are known by extension; the stat file is known only by its
    header, since the generator names it after its own pid. A set unions them,
    so a stat file that IS a .csv is counted once rather than twice.
    """
    files = {
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix in _CHECKSUMMED_SUFFIXES
    }
    stat_file = _find_stat_file(directory)
    if stat_file is not None:
        files.add(stat_file)
    return sorted(files, key=lambda p: p.name)


def _has_artifacts(directory: Path) -> bool:
    """Whether a directory holds either primary surface.

    The stat half asks the PARSER'S OWN finder rather than testing an extension.
    This gate used to look for ``*.csv`` while the parser behind it selected by
    header, so a real capture, whose stat file is named
    ``gossipper_<pid>_stats.log``, was turned away here and the parser was never
    reached: ``discover_tests`` returned [] and the caller was told there were no
    artifacts, standing in a directory holding a valid 43-column CSV.

    Calling the same function the parser calls is the point. Two places deciding
    "is this a stat file" by two criteria is what produced that, and a second
    rule written to agree with the first only agrees until one of them changes.
    """
    if (directory / SUMMARY_FILENAME).is_file():
        return True
    return _find_stat_file(directory) is not None


def discover_tests(directory: Path) -> list[Path]:
    """The test directories under ``directory``, in the order they ran.

    A directory holding artifacts directly is one test. Otherwise each
    immediate subdirectory holding artifacts is a test, ordered by name, which
    is what makes ``ramp-100``, ``ramp-200``, ``ramp-500`` sequence correctly.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise MissingArtifact(f"{directory}: not a directory")
    if _has_artifacts(directory):
        return [directory]
    return sorted(
        (p for p in directory.iterdir() if p.is_dir() and _has_artifacts(p)),
        key=lambda p: p.name,
    )


def checksum_artifacts(directories: list[Path]) -> str:
    """A sha256 over every artifact file, stable across runs and platforms.

    Names are hashed alongside contents so that renaming a file changes the
    checksum: two runs whose bytes match but whose layout differs were not
    imported from the same thing.
    """
    outer = hashlib.sha256()
    for directory in sorted(directories, key=lambda p: p.name):
        for path in _artifact_files(directory):
            outer.update(f"{directory.name}/{path.name}".encode())
            outer.update(b"\0")
            outer.update(hashlib.sha256(path.read_bytes()).digest())
    return outer.hexdigest()


def observations_for(parsed: ParsedTest) -> list[dict]:
    """The per-call observations these artifacts support. Today: none.

    Not a stub that will be filled in with something derived. The endpoint takes
    one call at a time and correlates on ``room_sid`` or ``attempt_id``, and
    neither exists anywhere in an aggregate summary or an interval CSV. The one
    file that would carry them has no documented record schema, so its contents
    are counted and never interpreted.

    Returning an empty list is the honest answer, and it is why an import
    reports zero observations posted rather than a number it made up.
    """
    return []


def _limitations_for(parsed: ParsedTest) -> list[str]:
    """What this test's artifacts could not answer, in an operator's words."""
    out: list[str] = []
    if parsed.peak_concurrency is None:
        out.append(
            f"{parsed.name}: no stat CSV, so peak concurrency is not measured. "
            "The summary's active_calls is an end-of-run drain value and is not "
            "a substitute for it."
        )
    if parsed.attempted_calls is None:
        out.append(f"{parsed.name}: no call totals, so establishment is not measured.")
    if not parsed.failures_by_cause:
        out.append(f"{parsed.name}: no failure breakdown by cause.")
    if parsed.answer_latency is None:
        out.append(
            f"{parsed.name}: no answer latency. It comes from rtd.answer, which "
            'requires start_rtd="answer" in the generator\'s scenario.'
        )
    if parsed.call_records_status == "absent":
        out.append(
            f"{parsed.name}: no per-call records, so no per-call observations "
            "were filed. The aggregate columns carry what these artifacts measured."
        )
    elif parsed.call_records_status == "present":
        out.append(
            f"{parsed.name}: {parsed.call_records_count} call records were found "
            f"but not interpreted. {CALL_RECORDS_FILENAME} has no documented "
            "record schema, so nothing was read out of them."
        )
    elif parsed.call_records_status == "unreadable":
        out.append(f"{parsed.name}: {CALL_RECORDS_FILENAME} could not be read.")
    return out


def build_plan(
    directory: Path,
    *,
    run_id: str | None = None,
    project: str = DEFAULT_PROJECT,
    label: str | None = None,
    captured: bool = False,
    now_ms: int,
    declared_targets: dict[str, int] | None = None,
) -> ImportPlan:
    """Read a directory of artifacts and plan the rows it would become.

    ``run_id`` defaults to the directory name so that re-importing the same
    artifacts updates the same run rather than creating a second one.

    ``captured`` is the operator asserting these artifacts came from a real run.
    Left False, the checksum is computed and recorded in the notes but not
    promoted to ``artifact_sha256``, so every report stamps itself synthetic.
    """
    directory = Path(directory)
    test_dirs = discover_tests(directory)
    if not test_dirs:
        raise MissingArtifact(
            f"{directory}: no artifacts here and no subdirectory holding any. "
            f"Expected {SUMMARY_FILENAME}, or a stat file whose header carries "
            "the interval columns. A stat file is recognised by that header "
            "whatever it is named, so its extension is not the problem."
        )

    parsed = [parse_test_directory(d) for d in test_dirs]
    checksum = checksum_artifacts(test_dirs)
    resolved_run_id = run_id or directory.name

    starts = [p.started_at_ms for p in parsed if p.started_at_ms is not None]
    ends = [p.ended_at_ms for p in parsed if p.ended_at_ms is not None]

    tools = {p.tool for p in parsed if p.tool}
    versions = {p.tool_version for p in parsed if p.tool_version}
    schemas = {p.artifact_schema_version for p in parsed if p.artifact_schema_version}

    notes = f"artifact sha256: {checksum}"
    if not captured:
        notes += (
            "\nImported WITHOUT --captured, so this run is synthetic and every "
            "report built from it stamps itself so. Re-import with --captured "
            "once these artifacts come from a real run."
        )

    run = LoadRunInput(
        id=resolved_run_id,
        created_at_ms=now_ms,
        project=project,
        label=label,
        # Only assert one tool when the tests agree. Two different generators in
        # one run is a fact worth not flattening into whichever sorted first.
        tool=next(iter(tools)) if len(tools) == 1 else None,
        tool_version=next(iter(versions)) if len(versions) == 1 else None,
        artifact_schema_version=next(iter(schemas)) if len(schemas) == 1 else None,
        started_at_ms=min(starts) if starts else None,
        ended_at_ms=max(ends) if ends else None,
        artifact_sha256=checksum if captured else None,
        artifact_source=str(directory.resolve()),
        notes=notes,
    )

    tests = [
        LoadRunTestInput(
            run_id=resolved_run_id,
            name=p.name,
            created_at_ms=now_ms,
            sequence=index,
            started_at_ms=p.started_at_ms,
            ended_at_ms=p.ended_at_ms,
            peak_concurrency=p.peak_concurrency,
            attempted_calls=p.attempted_calls,
            succeeded_calls=p.succeeded_calls,
            failed_calls=p.failed_calls,
            # Absent causes stay absent: a cause the artifact did not carry is
            # None, not 0, so nothing claims there were no timeouts.
            failed_timeout=p.failures_by_cause.get("timeout"),
            failed_unexpected_sip=p.failures_by_cause.get("unexpected_sip"),
            failed_transport_error=p.failures_by_cause.get("transport_error"),
            failed_parse_error=p.failures_by_cause.get("parse_error"),
            failed_scenario_error=p.failures_by_cause.get("scenario_error"),
            failed_cancelled=p.failures_by_cause.get("cancelled"),
            rtp_packets_sent=p.rtp_packets_sent,
            rtp_packets_received=p.rtp_packets_received,
            # What the step ASKED for is not in any artifact: it lives in the
            # generator's scenario file. It is None unless the operator declared
            # it, and a declaration is kept apart from a measurement: the peak
            # concurrency above still comes from the stat file and is never
            # overwritten by this.
            target_concurrency=(declared_targets or {}).get(p.name),
            # The node CPU/memory peaks and the sample count stay None here:
            # they are correlated from node_samples over each test's window.
        )
        for index, p in enumerate(parsed)
    ]

    limitations: list[str] = []
    for p in parsed:
        limitations.extend(_limitations_for(p))

    return ImportPlan(run=run, tests=tests, parsed=parsed, limitations=limitations)


__all__ = [
    "ImportPlan",
    "build_plan",
    "checksum_artifacts",
    "discover_tests",
    "observations_for",
]
