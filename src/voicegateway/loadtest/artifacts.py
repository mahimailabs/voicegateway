"""Pure parsers for an external load generator's run artifacts.

**The shape read here is UNVERIFIED against a real artifact.** Every field name
below was taken from the generator's published schema documentation, and the
fixtures these parsers are tested against were hand-built from those same
documents rather than captured from a run. Those fixtures prove this parser is
defensive. They do NOT prove it matches what the generator actually writes.
Nothing derived from them may be presented as measured: a report built from them
carries ``data_provenance: synthetic`` until a captured artifact replaces them.

Nothing in this module reads the generator's source. It is AGPL-3.0 and this
project is MIT, so the binary runs separately and only its output files cross the
boundary. Recovering an undocumented shape by reading that source is not an
option available here, which is why an undocumented surface is skipped outright
rather than guessed at.

Two surfaces, and which column comes from which
-----------------------------------------------

``summary.json`` carries run totals. The ``-trace_stat`` CSV carries one row per
interval. Between them they hold every column the load-test report requires, but
they are not interchangeable, and taking a column from the wrong one produces a
plausible number rather than an error:

* **Peak concurrency** comes from ``max(active_calls)`` across CSV rows.
  ``summary.json`` has an ``active_calls`` too, but it is the END-OF-RUN DRAIN
  value and reads 0 once the last call has hung up. Sourcing concurrency from the
  summary reports 0 concurrency for a run that held hundreds.

* **Establishment** comes from the run-total COUNTS, ``total_calls`` and
  ``success_calls``. Never from a scan over the CSV's per-row ``success_ratio``:
  the first interval legitimately reads 0, because calls are in flight and none
  has completed yet, so a ``min()`` over that column fails a healthy run.

* **Failures by cause** are shaped differently on each surface. The summary NESTS
  them under ``failure_classes``; the CSV carries them FLAT as ``failure_<cause>``
  with per-interval ``delta_failure_<cause>`` beside them. Both normalise to the
  same six cause names here.

What is deliberately not read
-----------------------------

``health.passed`` is the generator's own verdict against its own configured
``min_success_ratio``, and that threshold is routinely set to the same value the
acceptance gate uses. Reading it would make the gate layer a passthrough for the
generator's self-assessment, and the passthrough would be invisible precisely
because the two numbers agree. This module parses no ``health`` key at all;
:mod:`voicegateway.livekit_diag.gates` judges independently from the counts.

The generator's own ``success_ratio`` IS parsed, but only as
:attr:`ParsedTest.reported_success_ratio`, never as the verdict. It exists so a
caller can cross-check it against the ratio the counts imply and notice an import
that misread one of them.

``calls.jsonl`` is optional enrichment and its absence is never an error. Its
per-record schema was recovered by capturing a real run and is now mapped, but
only far enough to answer one question the aggregates cannot: how many calls
answered, sent audio, and got NONE back. A run total of packets received hides
that completely, because half the calls carrying full audio and half carrying
none averages to a ratio that looks merely degraded.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The only summary shape this parser claims to understand. Anything else raises
# rather than being read on a best-effort basis: a generator that changed its
# schema is far more likely to have moved a field than to have kept every one
# this module needs, and a half-read summary produces numbers nobody can trace.
SUPPORTED_SUMMARY_SCHEMA = "gossipper_summary_v1"

# The only per-call record shape whose FIELDS are read. Unlike the summary, an
# unrecognised version here does not raise: this surface is enrichment and an
# import must survive without it. It does mean the media tallies below stay
# None, which every consumer must render as not-measured rather than as zero.
SUPPORTED_CALL_RECORD_SCHEMA = "gossipper_call_record_v1"

# The canonical cause names, matching the ``failed_*`` columns on
# ``load_run_tests``. Both surfaces normalise onto these.
FAILURE_CAUSES = (
    "timeout",
    "unexpected_sip",
    "transport_error",
    "parse_error",
    "scenario_error",
    "cancelled",
)

# Columns the CSV must carry to be recognised at all. Deliberately the minimum
# the load-test report needs, not the full 43-column header: a generator that
# adds or drops an unrelated column should still import.
REQUIRED_STAT_COLUMNS = frozenset(
    {
        "elapsed_ms",
        "total_calls",
        "success_calls",
        "failed_calls",
        "active_calls",
    }
)

# Discovery names. The stat file is whatever ``-trace_stat`` was pointed at, so
# it is found by reading headers rather than by name or extension: an observed
# run wrote "gossipper_<pid>_stats.log", which no extension-based match finds.
SUMMARY_FILENAME = "summary.json"
CALL_RECORDS_FILENAME = "calls.jsonl"

# Skipped during stat discovery because they are known to be OTHER surfaces.
# Named explicitly rather than filtered by extension, which is the mistake this
# discovery exists to avoid making twice.
_NOT_STAT_FILES: frozenset[str] = frozenset({SUMMARY_FILENAME, CALL_RECORDS_FILENAME})

# How much of a file to read while deciding whether it is the stat file. The
# real header is ~700 bytes; this is generous and still bounded, so a directory
# containing something enormous costs one small read.
_STAT_HEADER_MAX_BYTES = 64 * 1024


class ArtifactError(Exception):
    """Base for every failure to read a load generator's artifacts."""


class MissingArtifact(ArtifactError):
    """Neither primary surface was present.

    Raised only when BOTH ``summary.json`` and the stat CSV are absent. Either
    one alone is a usable import; the caller is told which columns it will be
    missing rather than being refused.
    """


class UnknownArtifactSchema(ArtifactError):
    """An artifact announced a shape this parser does not claim to understand.

    This is the named error the defensive contract calls for. It fires on the
    summary's ``schema_version``, and on a stat CSV whose header is missing
    columns the report needs.
    """


class MalformedArtifact(ArtifactError):
    """An artifact had the right shape but an unreadable value in it.

    Distinct from :class:`UnknownArtifactSchema`: the shape was recognised, so
    this is a corrupt or truncated file rather than a version this parser has
    not seen.
    """


def _nested(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """A nested object, or an empty one when the artifact did not carry it.

    An absent section and a section holding something other than an object are
    treated alike: neither yields fields, and neither is worth failing an import
    over when every field beneath it is optional anyway.
    """
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _as_int(value: Any, *, where: str) -> int | None:
    """An integer, or None when the artifact did not carry one.

    Empty and absent both mean "not measured" and become None. A value that is
    PRESENT but unreadable is a malformed artifact, not a None: silently
    dropping it would turn a corrupt file into a missing measurement, and those
    warrant different responses.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # float() first so "4.0" and "4e3" survive; the generator writes counts
        # as integers, but a CSV that rounds through a float is still readable.
        parsed = float(text)
    except ValueError as exc:
        raise MalformedArtifact(f"{where}: {value!r} is not a number") from exc
    # float() ACCEPTS "nan", "inf" and "Infinity", and json.loads accepts the
    # bare NaN/Infinity tokens, so both reach here having parsed cleanly. int()
    # then raises ValueError on NaN and OverflowError on an infinity, neither of
    # which is an ArtifactError, so a corrupt artifact would escape this
    # module's own error hierarchy and surface as an unhandled crash.
    if not math.isfinite(parsed):
        raise MalformedArtifact(f"{where}: {value!r} is not a finite number")
    if parsed != int(parsed):
        raise MalformedArtifact(f"{where}: {value!r} is not a whole number")
    return int(parsed)


def _as_float(value: Any, *, where: str) -> float | None:
    """A float, or None when the artifact did not carry one.

    NaN and the infinities are refused rather than stored. A NaN is worse than
    useless here: every comparison against it is False, so a NaN
    ``success_ratio`` would make :attr:`ParsedTest.reported_ratio_disagrees`
    answer "no disagreement" and the cross-check that exists to catch a misread
    import would silently pass on the one input it most needs to catch.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise MalformedArtifact(f"{where}: {value!r} is not a number") from exc
    if not math.isfinite(parsed):
        raise MalformedArtifact(f"{where}: {value!r} is not a finite number")
    return parsed


def _as_epoch_ms(value: Any, *, where: str) -> int | None:
    """An ISO-8601 instant as epoch milliseconds, or None when absent.

    A timestamp that is present but unparseable raises, for the same reason
    :func:`_as_int` raises: an unreadable clock is a broken artifact, not an
    unmeasured one.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # fromisoformat handles a "Z" suffix natively on the versions this project
    # supports; normalising anyway costs nothing and keeps the parse working if
    # the floor ever drops.
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise MalformedArtifact(f"{where}: {value!r} is not an ISO-8601 time") from exc
    if moment.tzinfo is None:
        # A naive stamp is read as UTC. The generator writes Z-suffixed times;
        # assuming local time would silently shift a run's window by hours and
        # break correlation against node samples scraped in UTC.
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


@dataclass(frozen=True)
class AnswerLatency:
    """The INVITE-to-200-OK wall time the generator measured.

    This is the strongest answer-latency evidence available anywhere in this
    system, because the process reporting it placed the INVITE and saw the 200
    OK itself. It maps to the ``sipp_rtd`` source, the top rung of the answer
    latency ladder in :mod:`voicegateway.repository.calls_repository` and the
    only source a caller is permitted to report directly.

    Every field is optional: a run that did not configure answer timing carries
    none of them, which is different from measuring zero.
    """

    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    last_ms: float | None = None
    stddev_ms: float | None = None
    # Bucket label to count, exactly as the generator labelled them. Not
    # reinterpreted into percentiles here: the bucket edges are the generator's
    # and inventing a percentile from them would fabricate precision.
    buckets: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class IntervalSample:
    """One row of the ``-trace_stat`` CSV: the run's state at one instant.

    ``active_calls`` is the reason this surface exists. It is the only place a
    run's concurrency over time is recorded, and the run total in the summary
    cannot substitute for it.
    """

    elapsed_ms: int | None
    at_ms: int | None
    total_calls: int | None
    success_calls: int | None
    failed_calls: int | None
    active_calls: int | None
    success_ratio: float | None
    calls_per_second: float | None
    rtp_packets_sent: int | None
    rtp_packets_received: int | None
    # None when this CSV carries no failure_* column at all, as distinct from
    # carrying them with nothing recognised in them.
    failures_by_cause: dict[str, int] | None = None


@dataclass(frozen=True)
class RunSummary:
    """``summary.json``: the run's totals, as the generator reported them."""

    schema_version: str
    tool_version: str | None
    started_at_ms: int | None
    finished_at_ms: int | None
    elapsed_ms: int | None
    total_calls: int | None
    success_calls: int | None
    failed_calls: int | None
    # The generator's own ratio. Kept for cross-checking against the counts,
    # never used as a verdict. See the module docstring.
    reported_success_ratio: float | None
    retransmits: int | None
    timeouts: int | None
    # None when summary.json carried no failure_classes section at all.
    failures_by_cause: dict[str, int] | None = None
    rtp_packets_sent: int | None = None
    rtp_packets_received: int | None = None
    answer_latency: AnswerLatency | None = None


@dataclass(frozen=True)
class ParsedTest:
    """One test's artifacts, with each column taken from the correct surface.

    Every field is optional. A column the artifacts did not carry stays None,
    which a caller must render as not-measured rather than as zero.
    """

    name: str
    tool: str | None = None
    tool_version: str | None = None
    artifact_schema_version: str | None = None

    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    duration_ms: int | None = None

    # From max(active_calls) across the CSV. NEVER from the summary, whose
    # active_calls is the end-of-run drain value.
    peak_concurrency: int | None = None

    # Run totals. These, not any ratio, are what the establishment gate judges.
    attempted_calls: int | None = None
    succeeded_calls: int | None = None
    failed_calls: int | None = None

    failures_by_cause: dict[str, int] = field(default_factory=dict)

    rtp_packets_sent: int | None = None
    rtp_packets_received: int | None = None

    answer_latency: AnswerLatency | None = None

    # The generator's self-reported ratio, for cross-checking only.
    reported_success_ratio: float | None = None

    samples: tuple[IntervalSample, ...] = ()

    # "absent" | "present" | "unreadable". Never blocks an import: the record
    # schema is undocumented, so this says only whether the file was there and
    # readable, not what was in it.
    call_records_status: str = "absent"
    call_records_count: int | None = None

    # Per-call media, from calls.jsonl. None means the records were absent,
    # unreadable, or of a schema this parser does not map; it NEVER means zero.
    #
    # These exist because the run totals cannot answer the question. A test
    # where every call carries full audio and one where half the calls carry
    # none and half carry double produce the same rtp_packets_received. The
    # second is a failed run and the first is not, and only a per-call count
    # tells them apart. Observed: a 24 hour run reported 100% establishment,
    # zero failures and a 0.496 packet ratio while 12,198 of 28,804 calls had
    # received nothing at all.
    calls_answered_with_inbound: int | None = None
    calls_answered_without_inbound: int | None = None

    @property
    def establishment_ratio(self) -> float | None:
        """Succeeded over attempted, computed from the counts.

        None when attempts were not measured, and None when zero calls were
        attempted: a run that placed no calls has no establishment rate, and
        returning 0.0 would fail a gate for a run that never ran.

        This is the number to judge. :attr:`reported_success_ratio` is the
        generator grading its own homework and is here only to be compared
        against this one.
        """
        if self.attempted_calls is None or self.succeeded_calls is None:
            return None
        if self.attempted_calls <= 0:
            return None
        return self.succeeded_calls / self.attempted_calls

    @property
    def reported_ratio_disagrees(self) -> bool:
        """Whether the generator's ratio contradicts its own counts.

        True only when both are present and they differ by more than rounding.
        A True here means one of the two was misread on import and neither
        should be trusted until a human looks.
        """
        computed = self.establishment_ratio
        if computed is None or self.reported_success_ratio is None:
            return False
        # The generator writes the ratio rounded; 0.999 against 14985/15000 is
        # agreement, not disagreement.
        return abs(computed - self.reported_success_ratio) > 1e-3


def _failures_from_mapping(raw: Any, *, where: str) -> dict[str, int] | None:
    """Normalise the summary's NESTED ``failure_classes`` onto the cause names.

    Returns None when the section is ABSENT, and a mapping when it is present.
    Those are different facts and the caller acts on them differently: an absent
    section means this surface cannot answer the question, while a present but
    empty one means it answered "no recognised causes". Collapsing both to ``{}``
    is what lets a fallback fire against a summary that did answer.

    Within a present section, only causes actually carried are returned. A cause
    the artifact omitted is absent from the mapping rather than zero, because
    "the artifact carried no breakdown" and "there were no timeouts" are
    different claims and only the second may be rendered as a zero.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for cause in FAILURE_CAUSES:
        value = _as_int(raw.get(cause), where=f"{where}.{cause}")
        if value is not None:
            out[cause] = value
    return out


def _failures_from_row(row: dict[str, Any], *, where: str) -> dict[str, int] | None:
    """Normalise the CSV's FLAT ``failure_<cause>`` columns onto the same names.

    None when the CSV carries no ``failure_*`` column at all, mirroring
    :func:`_failures_from_mapping`: this surface cannot answer, as distinct from
    answering that nothing was recognised.

    The ``delta_failure_<cause>`` columns beside them are per-interval and are
    deliberately not read here: these are cumulative totals, and reading a
    delta as the total under-reports every interval after the first.
    """
    if not any(f"failure_{cause}" in row for cause in FAILURE_CAUSES):
        return None
    out: dict[str, int] = {}
    for cause in FAILURE_CAUSES:
        value = _as_int(row.get(f"failure_{cause}"), where=f"{where}.failure_{cause}")
        if value is not None:
            out[cause] = value
    return out


def parse_summary(path: Path) -> RunSummary:
    """Read ``summary.json``.

    Raises :class:`UnknownArtifactSchema` when ``schema_version`` is anything
    other than the one shape this parser claims to understand.
    """
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise MalformedArtifact(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise UnknownArtifactSchema(f"{path}: expected a JSON object")

    declared = raw.get("schema_version")
    if declared != SUPPORTED_SUMMARY_SCHEMA:
        raise UnknownArtifactSchema(
            f"{path}: schema_version is {declared!r}, and this parser only "
            f"understands {SUPPORTED_SUMMARY_SCHEMA!r}. The shape was taken from "
            "published schema documentation and has not been verified against a "
            "captured artifact, so reading an unknown version on a best-effort "
            "basis would produce untraceable numbers."
        )

    media = _nested(raw, "media")
    answer = _nested(_nested(raw, "rtd"), "answer")

    latency = None
    if answer:
        buckets: dict[str, int] = {}
        for label, count in _nested(answer, "buckets").items():
            parsed = _as_int(count, where=f"rtd.answer.buckets[{label}]")
            if parsed is not None:
                buckets[str(label)] = parsed
        latency = AnswerLatency(
            avg_ms=_as_float(answer.get("avg"), where="rtd.answer.avg"),
            min_ms=_as_float(answer.get("min"), where="rtd.answer.min"),
            max_ms=_as_float(answer.get("max"), where="rtd.answer.max"),
            last_ms=_as_float(answer.get("last"), where="rtd.answer.last"),
            stddev_ms=_as_float(answer.get("stddev"), where="rtd.answer.stddev"),
            buckets=buckets,
        )

    return RunSummary(
        schema_version=str(declared),
        tool_version=(
            str(raw["tool_version"]) if raw.get("tool_version") is not None else None
        ),
        started_at_ms=_as_epoch_ms(raw.get("started_at"), where="started_at"),
        finished_at_ms=_as_epoch_ms(raw.get("finished_at"), where="finished_at"),
        elapsed_ms=_as_int(raw.get("elapsed_ms"), where="elapsed_ms"),
        total_calls=_as_int(raw.get("total_calls"), where="total_calls"),
        success_calls=_as_int(raw.get("success_calls"), where="success_calls"),
        failed_calls=_as_int(raw.get("failed_calls"), where="failed_calls"),
        reported_success_ratio=_as_float(
            raw.get("success_ratio"), where="success_ratio"
        ),
        retransmits=_as_int(raw.get("retransmits"), where="retransmits"),
        timeouts=_as_int(raw.get("timeouts"), where="timeouts"),
        failures_by_cause=_failures_from_mapping(
            raw.get("failure_classes"), where="failure_classes"
        ),
        rtp_packets_sent=_as_int(
            media.get("rtp_packets_sent"), where="media.rtp_packets_sent"
        ),
        rtp_packets_received=_as_int(
            media.get("rtp_packets_received"), where="media.rtp_packets_received"
        ),
        answer_latency=latency,
    )


def parse_stat_csv(path: Path) -> list[IntervalSample]:
    """Read the ``-trace_stat`` CSV into one sample per interval.

    Columns are recognised BY NAME, never by position, so a generator that adds
    a column still imports. Raises :class:`UnknownArtifactSchema` when the header
    is missing a column the report needs.
    """
    try:
        # utf-8-sig, not utf-8: a BOM would otherwise attach to the first header
        # field, turning "timestamp" into "\ufefftimestamp". That column is not
        # required, so nothing would raise, and every row's at_ms would silently
        # read None while the column sat there full of valid data.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise MalformedArtifact(f"{path}: cannot be read: {exc}") from exc

    reader = csv.DictReader(text.splitlines())
    fieldnames = list(reader.fieldnames or ())
    duplicates = sorted({c for c in fieldnames if fieldnames.count(c) > 1})
    if duplicates:
        # DictReader keeps only the last occurrence, so a duplicated column
        # means the value read is whichever came last, with nothing to say so.
        raise UnknownArtifactSchema(
            f"{path}: stat CSV header repeats {duplicates}. Only the last "
            "occurrence of a repeated column survives, so which value was read "
            "would be unknowable."
        )
    header = set(fieldnames)
    missing = REQUIRED_STAT_COLUMNS - header
    if missing:
        raise UnknownArtifactSchema(
            f"{path}: stat CSV is missing required columns {sorted(missing)}. "
            f"Recognised {len(header)} columns."
        )

    samples: list[IntervalSample] = []
    for index, row in enumerate(reader):
        where = f"{path}:row{index + 1}"
        samples.append(
            IntervalSample(
                elapsed_ms=_as_int(row.get("elapsed_ms"), where=f"{where}.elapsed_ms"),
                at_ms=_as_epoch_ms(row.get("timestamp"), where=f"{where}.timestamp"),
                total_calls=_as_int(
                    row.get("total_calls"), where=f"{where}.total_calls"
                ),
                success_calls=_as_int(
                    row.get("success_calls"), where=f"{where}.success_calls"
                ),
                failed_calls=_as_int(
                    row.get("failed_calls"), where=f"{where}.failed_calls"
                ),
                active_calls=_as_int(
                    row.get("active_calls"), where=f"{where}.active_calls"
                ),
                success_ratio=_as_float(
                    row.get("success_ratio"), where=f"{where}.success_ratio"
                ),
                calls_per_second=_as_float(
                    row.get("calls_per_second"), where=f"{where}.calls_per_second"
                ),
                rtp_packets_sent=_as_int(
                    row.get("rtp_packets_sent"), where=f"{where}.rtp_packets_sent"
                ),
                rtp_packets_received=_as_int(
                    row.get("rtp_packets_received"),
                    where=f"{where}.rtp_packets_received",
                ),
                failures_by_cause=_failures_from_row(row, where=where),
            )
        )
    return samples


def _looks_like_stat_file(path: Path) -> bool:
    """Does this file's FIRST LINE parse as a stat header?

    Reads a bounded prefix, so pointing this at a directory holding a multi-GB
    packet capture costs one small read rather than the whole file. Anything
    that is not decodable text, or whose header does not carry every required
    column, is not a candidate.
    """
    try:
        with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
            first = handle.readline(_STAT_HEADER_MAX_BYTES)
    except (OSError, UnicodeDecodeError):
        # Unreadable or binary. Not an error: discovery looks at every file in
        # the directory, so most of what it sees is expected not to qualify.
        return False
    if not first.strip():
        return False
    try:
        header = next(csv.reader([first]))
    except (csv.Error, StopIteration):
        return False
    return REQUIRED_STAT_COLUMNS <= {column.strip() for column in header}


def _find_stat_file(directory: Path) -> Path | None:
    """The stat file in a directory, or None. Selected by HEADER, not by name.

    The generator writes ``gossipper_<pid>_stats.log``, and this used to glob
    ``*.csv``, so a real run's stat file was never even a candidate and the
    header check that would have recognised it never ran. The import then failed
    with "neither surface is present" while sitting in a directory containing a
    valid 43-column CSV. The file is a CSV by content and a ``.log`` by name, so
    the extension is ignored entirely and content decides.

    Files are considered in sorted order for determinism. ``calls.jsonl`` and
    ``summary.json`` are skipped by name because they are known to be other
    surfaces, not because of their extensions; everything else is judged by
    reading its first line.
    """
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name in _NOT_STAT_FILES:
            continue
        if _looks_like_stat_file(path):
            return path
    return None


@dataclass(frozen=True)
class CallRecords:
    """What ``calls.jsonl`` yielded, or why it yielded nothing."""

    #: "absent" | "present" | "unreadable"
    status: str
    count: int | None = None
    #: Calls that answered, sent RTP, and received some back.
    answered_with_inbound: int | None = None
    #: Calls that answered and sent RTP and received NONE. The number that
    #: matters: by this project's own definition a call that answered and
    #: carried no audio is a failure, not a success.
    answered_without_inbound: int | None = None


def _read_call_records(path: Path) -> CallRecords:
    """Read ``calls.jsonl``, tallying calls that answered but got no audio back.

    The schema was recovered by capturing a real run rather than by reading
    AGPL source, and is recorded here so the next person does not have to
    capture one:

        schema_version  "gossipper_call_record_v1"
        call_id         str      e.g. "gossip-1-1-8e31bd2e"
        call_number     int      1-based
        success         bool
        duration_ms     int
        error           str      empty on success
        media           object, keys in PascalCase:
                        RTPPacketsSent, RTPOctetsSent, RTPPacketsReceived,
                        RTCPSenderReports, RTCPReceiverReports,
                        RTCPPacketsReceived, RTCPReportBlocks,
                        RTCPMaxFractionLost, RTCPMaxJitter, RTCPMinJitter,
                        RTCPJitterSum, RTCPJitterSamples,
                        RTPRecvMaxCumulativeLost,
                        RTPRecvInterarrivalJitterPeak

    ONLY THREE FIELDS ARE READ, deliberately. Per-call jitter and loss belong to
    the observation surface, not to the per-test row this module builds, and
    mapping them here would put two owners on the same data. What is read is the
    minimum that answers a question no aggregate can: did this call get audio
    back at all.

    A call counts as silent only if it answered AND sent RTP AND received none.
    Each clause earns its place. A call that never answered is already counted
    by the establishment gate and counting it twice would double-punish one
    failure. A call that sent nothing has not demonstrated the return path was
    even exercised, so its silence is not evidence about the return path.

    Never raises, and an unknown schema_version is not an error: this surface is
    enrichment. It does leave the tallies None, which is not zero, and every
    consumer must render it as not-measured.
    """
    if not path.is_file():
        return CallRecords(status="absent")
    try:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    except (OSError, UnicodeError):
        # UnicodeDecodeError is a ValueError, NOT an OSError, so catching only
        # the latter let a non-UTF-8 file abort the whole import from a surface
        # this function's own docstring promises never raises.
        return CallRecords(status="unreadable")

    count = 0
    with_inbound = 0
    without_inbound = 0
    mapped = True
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return CallRecords(status="unreadable")
        count += 1
        if not isinstance(record, dict):
            mapped = False
            continue
        if record.get("schema_version") != SUPPORTED_CALL_RECORD_SCHEMA:
            # One unrecognised record disqualifies the tally rather than
            # silently reducing it. A partial count read as a total is the
            # failure mode this whole surface exists to catch.
            mapped = False
            continue
        media = _nested(record, "media")
        sent = _as_int(media.get("RTPPacketsSent"), where="media.RTPPacketsSent")
        received = _as_int(
            media.get("RTPPacketsReceived"), where="media.RTPPacketsReceived"
        )
        if record.get("success") is not True or not sent:
            continue
        if received:
            with_inbound += 1
        else:
            without_inbound += 1

    if not mapped:
        return CallRecords(status="present", count=count)
    return CallRecords(
        status="present",
        count=count,
        answered_with_inbound=with_inbound,
        answered_without_inbound=without_inbound,
    )


def parse_test_directory(directory: Path, *, name: str | None = None) -> ParsedTest:
    """Read one test's artifacts, taking each column from the correct surface.

    Either primary surface alone yields a usable import; only their joint
    absence raises :class:`MissingArtifact`. Columns the present surfaces cannot
    supply stay None.
    """
    directory = Path(directory)
    summary_path = directory / SUMMARY_FILENAME
    csv_path = _find_stat_file(directory)

    summary = parse_summary(summary_path) if summary_path.is_file() else None
    samples = parse_stat_csv(csv_path) if csv_path is not None else []

    if summary is None and not samples:
        raise MissingArtifact(
            f"{directory}: neither {SUMMARY_FILENAME} nor a stat file is present, so "
            "there is nothing to import. Either surface alone is enough. A stat "
            "file is recognised by its header carrying "
            f"{sorted(REQUIRED_STAT_COLUMNS)}, whatever it is named."
        )

    # Peak concurrency: max over the CSV's per-interval active_calls. The
    # summary's active_calls is the end-of-run drain value and is not a
    # fallback for this, at any cost: it would report 0 for a busy run.
    actives = [s.active_calls for s in samples if s.active_calls is not None]
    peak_concurrency = max(actives) if actives else None

    # Run totals come from the summary when it is present. Falling back to the
    # LAST CSV row is safe because these columns are cumulative, unlike
    # active_calls.
    last = samples[-1] if samples else None
    attempted = summary.total_calls if summary else None
    succeeded = summary.success_calls if summary else None
    failed = summary.failed_calls if summary else None
    if last is not None:
        attempted = last.total_calls if attempted is None else attempted
        succeeded = last.success_calls if succeeded is None else succeeded
        failed = last.failed_calls if failed is None else failed

    # The CSV supplies the breakdown ONLY on a summary-less import. Unlike the
    # cumulative scalars above, a breakdown can CONTRADICT a value the summary
    # did give: a summary reporting failed_calls=0 and omitting failure_classes,
    # paired with a CSV whose cumulative failure_timeout reads 5, would yield a
    # test that failed no calls and timed out five times. When the summary is
    # present it is the authority, and a section it omitted is not measured.
    failures = summary.failures_by_cause if summary else None
    if summary is None and last is not None:
        failures = last.failures_by_cause

    duration_ms = summary.elapsed_ms if summary else None
    if duration_ms is None and last is not None:
        duration_ms = last.elapsed_ms

    started = summary.started_at_ms if summary else None
    if started is None and samples:
        first_at = next((s.at_ms for s in samples if s.at_ms is not None), None)
        started = first_at
    ended = summary.finished_at_ms if summary else None
    if ended is None and last is not None:
        ended = last.at_ms

    rtp_sent = summary.rtp_packets_sent if summary else None
    rtp_recv = summary.rtp_packets_received if summary else None
    if last is not None:
        rtp_sent = last.rtp_packets_sent if rtp_sent is None else rtp_sent
        rtp_recv = last.rtp_packets_received if rtp_recv is None else rtp_recv

    tool = tool_version = None
    if summary and summary.tool_version:
        # "gossipper 0.1.62" -> ("gossipper", "0.1.62"). Split rather than
        # assumed: the whole string is kept as the version when it carries no
        # space, so nothing is invented for a differently-formatted build.
        parts = summary.tool_version.split(None, 1)
        tool = parts[0]
        tool_version = parts[1] if len(parts) > 1 else None

    records = _read_call_records(directory / CALL_RECORDS_FILENAME)

    return ParsedTest(
        name=name or directory.name,
        tool=tool,
        tool_version=tool_version,
        artifact_schema_version=summary.schema_version if summary else None,
        started_at_ms=started,
        ended_at_ms=ended,
        duration_ms=duration_ms,
        peak_concurrency=peak_concurrency,
        attempted_calls=attempted,
        succeeded_calls=succeeded,
        failed_calls=failed,
        failures_by_cause=dict(failures) if failures else {},
        rtp_packets_sent=rtp_sent,
        rtp_packets_received=rtp_recv,
        answer_latency=summary.answer_latency if summary else None,
        reported_success_ratio=summary.reported_success_ratio if summary else None,
        samples=tuple(samples),
        call_records_status=records.status,
        call_records_count=records.count,
        calls_answered_with_inbound=records.answered_with_inbound,
        calls_answered_without_inbound=records.answered_without_inbound,
    )
