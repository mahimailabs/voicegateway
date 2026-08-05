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


#: Heading under which a run's own gaps are recorded in ``load_runs.notes``, and
#: the prefix each one carries. The report is exported by a LATER command, in a
#: different process, reading the row rather than the artifacts, so a limitation
#: computed at import and not written down is one nobody downstream can see.
#:
#: Stored as text in the notes column rather than behind a new column, because
#: notes already carries exactly this kind of human-readable fact (the artifact
#: checksum, the synthetic warning) and a migration to hold prose is a poor
#: trade. The round trip is pinned by a test, since the cost of the choice is
#: that the format is load-bearing.
LIMITATIONS_HEADING = "Not measured by these artifacts:"
_LIMITATION_PREFIX = "- "


#: Heading under which a DECLARED ramp plan is recorded in ``load_runs.notes``,
#: and the prefix each step carries. Same mechanism and same reason as
#: :data:`LIMITATIONS_HEADING`: the report is exported by a later command
#: reading the row, and ``rate_per_second`` has no column to live in.
#:
#: ``target_concurrency`` is ALSO written to its own column, because it has one.
#: This records the whole declaration in one place so the report can say which
#: figures rest on it.
PLAN_HEADING = "Declared ramp plan (operator-supplied, not measured):"
_PLAN_PREFIX = "- "


def plan_to_notes(plan: dict[str, dict[str, float]]) -> str:
    """The declared plan as the text appended to a run's notes."""
    lines = []
    for name in sorted(plan):
        step = plan[name]
        parts = [f"target={step['target_concurrency']}"]
        if step.get("rate_per_second") is not None:
            parts.append(f"rate={step['rate_per_second']}")
        if step.get("hold_seconds") is not None:
            parts.append(f"hold={step['hold_seconds']}")
        lines.append(f"{_PLAN_PREFIX}{name}: " + " ".join(parts))
    return f"\n\n{PLAN_HEADING}\n" + "\n".join(lines)


def plan_from_notes(notes: str | None) -> dict[str, dict[str, float]]:
    """The declared plan, read back out of ``load_runs.notes``.

    Returns {} when nothing was declared, which is what makes capacity refuse
    rather than guess. An unparseable line is skipped rather than raising: a
    malformed note must not stop a report being exported.
    """
    if not notes or PLAN_HEADING not in notes:
        return {}
    _, _, tail = notes.partition(PLAN_HEADING)
    out: dict[str, dict[str, float]] = {}
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(_PLAN_PREFIX):
            break
        body = stripped[len(_PLAN_PREFIX) :]
        name, _, rest = body.partition(":")
        step: dict[str, float] = {}
        for token in rest.split():
            key, _, raw = token.partition("=")
            field = {
                "target": "target_concurrency",
                "rate": "rate_per_second",
                "hold": "hold_seconds",
            }.get(key)
            if field is None:
                continue
            try:
                step[field] = float(raw) if field != "target_concurrency" else int(raw)
            except ValueError:
                continue
        if step.get("target_concurrency") is not None:
            out[name.strip()] = step
    return out


#: Heading under which a DECLARED per-node network baseline is recorded in
#: ``load_runs.notes``, the prefix each node carries, and the sentence written
#: under them. Same mechanism and same reason as :data:`PLAN_HEADING`: the report
#: is exported by a later command reading the row, and a figure nothing measured
#: has no column to live in.
#:
#: This one is declared because there is nothing to measure it WITH. The ENA
#: driver on these instances reports no link speed, so ``node_network_speed_bytes``
#: yields nothing for the interface the traffic actually crosses, and a bandwidth
#: utilisation without a denominator is not a number. The only figure available
#: is the one AWS publishes per instance type, and it is published in a document
#: rather than on the host, so an operator is the only thing that can carry it
#: across.
#:
#: Published, for orientation only: c7i.2xlarge is a 3.125 Gbps baseline with a
#: 12.5 Gbps burst peak, c6i.xlarge is 1.562 Gbps with the same 12.5 peak.
#:
#: There is deliberately NO instance-type-to-bandwidth mapping anywhere in this
#: code. Writing one would mean a node whose type nobody checked still gets a
#: denominator, from a table that was never confirmed against the machine the run
#: was on, and the gate would then print a confident percentage where it owes an
#: UNKNOWN. A node with no line under this heading has no baseline, and that has
#: to stay visible.
NETWORK_BASELINE_HEADING = (
    "Declared network baseline (operator-supplied published figure, not measured):"
)
_NETWORK_BASELINE_PREFIX = "- "
_NETWORK_BASELINE_CAVEAT = (
    "These are the instance type's PUBLISHED baseline, in bits per second, "
    "declared by an operator at import. Nothing here measured them. The instance "
    "can burst above its baseline, so each figure is a FLOOR on the link and not "
    "a ceiling: utilisation computed against it over-reads rather than under-reads."
)


def network_baselines_to_notes(baselines: dict[str, dict[str, float]]) -> str:
    """The declared network baselines as the text appended to a run's notes.

    Keyed by NODE, not by test. A ramp step is a slice of time and every node in
    the fleet is under it; the link is a property of the machine, and two nodes
    in one run are routinely different instance types with different baselines.

    Both directions are always written. Ingress and egress are separate
    allowances on these instances and separate counters on the host, and one
    figure standing in for both would be an assumption in the middle of a
    declaration whose whole purpose is to carry no assumptions.

    The float is written as-is rather than rounded into Gbps, because the read
    side has to return the same number the operator declared: a report that
    quotes a denominator different from the one the gate divided by is worse than
    one that quotes none.
    """
    lines = []
    for node in sorted(baselines):
        declared = baselines[node]
        lines.append(
            f"{_NETWORK_BASELINE_PREFIX}{node}: "
            f"in_bps={declared['in_bps']} out_bps={declared['out_bps']}"
        )
    return (
        f"\n\n{NETWORK_BASELINE_HEADING}\n"
        + "\n".join(lines)
        + f"\n{_NETWORK_BASELINE_CAVEAT}"
    )


def network_baselines_from_notes(notes: str | None) -> dict[str, dict[str, float]]:
    """The declared network baselines, read back out of ``load_runs.notes``.

    Returns {} when nothing was declared, which is what makes the headroom gate
    report UNKNOWN for a node rather than divide its throughput by a number
    nobody supplied. Nothing in this function invents, defaults or infers a
    baseline for an undeclared node, and nothing may be added that does: the
    absent case is the common one, and it is the one an operator has to be able
    to see in the report.

    An unparseable line is skipped rather than raising, exactly as in
    :func:`plan_from_notes`: a malformed note must not stop a report exporting.

    A node is kept only when BOTH directions parsed. Half a declaration would
    let ingress be judged while egress quietly vanished, and in a report "nothing
    scraped this" and "nothing declared a denominator for this" would then look
    identical. Dropping the node reports UNKNOWN for both directions, which
    under-claims in the safe direction.
    """
    if not notes or NETWORK_BASELINE_HEADING not in notes:
        return {}
    _, _, tail = notes.partition(NETWORK_BASELINE_HEADING)
    out: dict[str, dict[str, float]] = {}
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(_NETWORK_BASELINE_PREFIX):
            # The caveat sentence, or a later section. Either way this one ended.
            break
        body = stripped[len(_NETWORK_BASELINE_PREFIX) :]
        name, _, rest = body.partition(":")
        entry: dict[str, float] = {}
        for token in rest.split():
            key, _, raw = token.partition("=")
            if key not in ("in_bps", "out_bps"):
                continue
            try:
                entry[key] = float(raw)
            except ValueError:
                continue
        if "in_bps" in entry and "out_bps" in entry:
            out[name.strip()] = entry
    return out


def limitations_from_notes(notes: str | None) -> list[str]:
    """The run's own gaps, read back out of ``load_runs.notes``.

    Returns [] for a row written before this was recorded, which reads as "no
    gaps were noted" rather than as an error. That is the honest reading: the
    absence of a record is not a record of absence, and the structural limits
    list is unaffected either way.
    """
    if not notes or LIMITATIONS_HEADING not in notes:
        return []
    _, _, tail = notes.partition(LIMITATIONS_HEADING)
    out: list[str] = []
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.startswith(_LIMITATION_PREFIX):
            out.append(stripped[len(_LIMITATION_PREFIX) :])
        elif stripped:
            # A later section began. Stop rather than swallowing it.
            break
    return out


def _limitations_for(parsed: ParsedTest) -> list[str]:
    """What this test's artifacts could not answer, in an operator's words."""
    out: list[str] = []
    if parsed.peak_concurrency is None:
        out.append(
            f"{parsed.name}: no stat file, so peak concurrency is not measured. "
            "The summary's active_calls is an end-of-run drain value and is not "
            "a substitute for it."
        )
    if parsed.attempted_calls is None:
        out.append(f"{parsed.name}: no call totals, so establishment is not measured.")
    if not parsed.failures_by_cause:
        out.append(f"{parsed.name}: no failure breakdown by cause.")
    if parsed.answer_latency is None:
        # Two different causes, and naming the wrong one sends somebody to edit
        # a scenario file that is already correct. Answer latency comes from
        # rtd.answer, which the generator samples when a call reaches 200 OK. So
        # a run where nothing was answered produces none however the scenario is
        # written, and that is a fact about the run rather than the config.
        if parsed.succeeded_calls == 0 and (parsed.attempted_calls or 0) > 0:
            out.append(
                f"{parsed.name}: no answer latency, because none of the "
                f"{parsed.attempted_calls} calls reached 200 OK. rtd.answer is "
                "sampled when a call is answered, so nothing here could produce "
                "one. This says nothing about the generator's scenario."
            )
        elif parsed.succeeded_calls:
            out.append(
                f"{parsed.name}: no answer latency, although "
                f"{parsed.succeeded_calls} calls established. Calls were "
                "answered and no round-trip delay was reported, which points at "
                'the scenario: rtd.answer needs start_rtd="answer" on the INVITE '
                'and rtd="answer" on the response that ends the measurement.'
            )
        else:
            out.append(
                f"{parsed.name}: no answer latency, and no call totals either, "
                "so whether any call was answered is unknown. The cause cannot "
                "be attributed to the run or to the scenario from these "
                "artifacts."
            )
    if parsed.call_records_status == "absent":
        out.append(
            f"{parsed.name}: no per-call records, so no per-call observations "
            "were filed. The aggregate columns carry what these artifacts measured."
        )
    elif parsed.call_records_status == "present":
        out.append(
            f"{parsed.name}: {parsed.call_records_count} call records were found "
            f"but not interpreted. The {CALL_RECORDS_FILENAME} schema is known "
            "and recorded, and nothing maps it into per-call observations yet, "
            "so the aggregate columns carry what these artifacts measured."
        )
    elif parsed.call_records_status == "unreadable":
        out.append(f"{parsed.name}: {CALL_RECORDS_FILENAME} could not be read.")
    return out


def _declared_target(
    declared: dict[str, dict[str, float]] | None, name: str
) -> int | None:
    """The target concurrency an operator declared for one step, if any."""
    step = (declared or {}).get(name)
    if step is None:
        return None
    if isinstance(step, dict):
        value = step.get("target_concurrency")
        return int(value) if value is not None else None
    return int(step)


def build_plan(
    directory: Path,
    *,
    run_id: str | None = None,
    project: str = DEFAULT_PROJECT,
    label: str | None = None,
    captured: bool = False,
    now_ms: int,
    declared_targets: dict[str, dict[str, float]] | None = None,
    declared_network_baselines: dict[str, dict[str, float]] | None = None,
) -> ImportPlan:
    """Read a directory of artifacts and plan the rows it would become.

    ``run_id`` defaults to the directory name so that re-importing the same
    artifacts updates the same run rather than creating a second one.

    ``captured`` is the operator asserting these artifacts came from a real run.
    Left False, the checksum is computed and recorded in the notes but not
    promoted to ``artifact_sha256``, so every report stamps itself synthetic.

    ``declared_network_baselines`` is the published per-node link baseline, keyed
    by node. It is an operator's declaration for the same reason
    ``declared_targets`` is: nothing in the artifacts or on the host carries it.
    Left None, no baseline is recorded and none is invented, so the bandwidth
    headroom gate has no denominator and says so.
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

    if declared_targets:
        notes += plan_to_notes(
            {
                name: (step if isinstance(step, dict) else {"target_concurrency": step})
                for name, step in declared_targets.items()
            }
        )

    # Only written when something was declared. An empty section would read as a
    # fleet whose baselines are all known to be nothing, and the gate would have
    # to tell that apart from a run imported before this existed.
    if declared_network_baselines:
        notes += network_baselines_to_notes(declared_network_baselines)

    limitations: list[str] = []
    for p in parsed:
        limitations.extend(_limitations_for(p))
    if limitations:
        notes += f"\n\n{LIMITATIONS_HEADING}\n" + "\n".join(
            f"{_LIMITATION_PREFIX}{line}" for line in limitations
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
            calls_answered_with_inbound=p.calls_answered_with_inbound,
            calls_answered_without_inbound=p.calls_answered_without_inbound,
            # What the step ASKED for is not in any artifact: it lives in the
            # generator's scenario file. It is None unless the operator declared
            # it, and a declaration is kept apart from a measurement: the peak
            # concurrency above still comes from the stat file and is never
            # overwritten by this.
            target_concurrency=_declared_target(declared_targets, p.name),
            # The node CPU/memory peaks and the sample count stay None here:
            # they are correlated from node_samples over each test's window.
        )
        for index, p in enumerate(parsed)
    ]

    return ImportPlan(run=run, tests=tests, parsed=parsed, limitations=limitations)


__all__ = [
    "ImportPlan",
    "build_plan",
    "checksum_artifacts",
    "discover_tests",
    "observations_for",
]
