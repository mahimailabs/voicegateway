"""Async repo for ``load_runs`` and ``load_run_tests``.

Three properties this module holds:

* **Upserts read before they write.** Every write here is select-then-update,
  never a native ``ON CONFLICT``. That is the house rule for this schema, and it
  also makes a re-import of the same artifacts idempotent rather than a second
  set of rows: an operator who re-runs an import after fixing one field should
  end up with one run, not two.

* **NULL is never repaired.** A column is NULL because the artifact did not
  carry it. Nothing here substitutes 0, carries a value forward, or interpolates.
  A caller rendering these must suppress the value and label it not measured,
  because a 0 in ``peak_concurrency`` is a test that carried no calls.

* **Nothing derived is written.** Counts go in; rates are computed where they are
  judged. Storing an establishment rate beside the counts it came from creates
  two numbers that can disagree, and only one of them is evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from voicegateway.models.load_run_model import LoadRun, LoadRunTest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PROJECT = "default"

# How many runs one list read returns at most, so a caller cannot pull every run
# a long-lived deployment ever recorded by omitting a limit.
DEFAULT_RUN_LIMIT = 100


@dataclass(frozen=True)
class LoadRunInput:
    """One run as a caller supplies it.

    ``artifact_sha256`` is what a report reads to decide whether it may call
    itself measured, so it is left None unless a real artifact was hashed.
    """

    id: str
    created_at_ms: int
    project: str = DEFAULT_PROJECT
    label: str | None = None
    tool: str | None = None
    tool_version: str | None = None
    artifact_schema_version: str | None = None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    artifact_sha256: str | None = None
    artifact_source: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class LoadRunTestInput:
    """One test step as a caller supplies it.

    Every measured field defaults to None, so a caller that did not observe
    something simply does not set it. That is the difference between a test with
    no failures and a test whose failure breakdown was never parsed.
    """

    run_id: str
    name: str
    created_at_ms: int
    sequence: int = 0
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    target_concurrency: int | None = None
    peak_concurrency: int | None = None
    attempted_calls: int | None = None
    succeeded_calls: int | None = None
    failed_calls: int | None = None
    failed_timeout: int | None = None
    failed_unexpected_sip: int | None = None
    failed_transport_error: int | None = None
    failed_parse_error: int | None = None
    failed_scenario_error: int | None = None
    failed_cancelled: int | None = None
    peak_cpu_utilisation: float | None = None
    peak_memory_utilisation: float | None = None
    node_samples_in_window: int | None = None
    rtp_packets_sent: int | None = None
    rtp_packets_received: int | None = None
    calls_answered_with_inbound: int | None = None
    calls_answered_without_inbound: int | None = None


@dataclass(frozen=True)
class LoadRunRow:
    """One ``load_runs`` row as served to readers."""

    id: str
    project: str
    label: str | None
    tool: str | None
    tool_version: str | None
    artifact_schema_version: str | None
    started_at_ms: int | None
    ended_at_ms: int | None
    artifact_sha256: str | None
    artifact_source: str | None
    notes: str | None
    created_at_ms: int

    @property
    def has_artifact(self) -> bool:
        """Whether this run was built from a real artifact.

        The single input to a report's provenance. Derived from the checksum
        being present rather than stored as a flag, so nothing can assert
        measured-ness without holding the artifact that proves it.
        """
        return bool(self.artifact_sha256)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoadRunTestRow:
    """One ``load_run_tests`` row as served to readers."""

    id: int | None
    run_id: str
    name: str
    sequence: int
    started_at_ms: int | None
    ended_at_ms: int | None
    target_concurrency: int | None
    peak_concurrency: int | None
    attempted_calls: int | None
    succeeded_calls: int | None
    failed_calls: int | None
    failed_timeout: int | None
    failed_unexpected_sip: int | None
    failed_transport_error: int | None
    failed_parse_error: int | None
    failed_scenario_error: int | None
    failed_cancelled: int | None
    peak_cpu_utilisation: float | None
    peak_memory_utilisation: float | None
    node_samples_in_window: int | None
    rtp_packets_sent: int | None
    rtp_packets_received: int | None
    calls_answered_with_inbound: int | None
    calls_answered_without_inbound: int | None
    created_at_ms: int

    @property
    def duration_ms(self) -> int | None:
        """Wall duration, or None when either end was not recorded.

        None rather than 0: a test whose end was never written did not last no
        time.
        """
        if self.started_at_ms is None or self.ended_at_ms is None:
            return None
        span = self.ended_at_ms - self.started_at_ms
        return span if span >= 0 else None

    @property
    def failures_by_cause(self) -> dict[str, int]:
        """Only the causes that were actually parsed.

        A cause absent from this mapping was not measured. Returning every key
        with a 0 default would turn "the artifact carried no breakdown" into
        "there were no timeouts".
        """
        causes = {
            "timeout": self.failed_timeout,
            "unexpected_sip": self.failed_unexpected_sip,
            "transport_error": self.failed_transport_error,
            "parse_error": self.failed_parse_error,
            "scenario_error": self.failed_scenario_error,
            "cancelled": self.failed_cancelled,
        }
        return {k: v for k, v in causes.items() if v is not None}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_row(model: LoadRun) -> LoadRunRow:
    return LoadRunRow(
        id=model.id,
        project=model.project,
        label=model.label,
        tool=model.tool,
        tool_version=model.tool_version,
        artifact_schema_version=model.artifact_schema_version,
        started_at_ms=model.started_at_ms,
        ended_at_ms=model.ended_at_ms,
        artifact_sha256=model.artifact_sha256,
        artifact_source=model.artifact_source,
        notes=model.notes,
        created_at_ms=model.created_at_ms,
    )


_TEST_FIELDS = (
    "sequence",
    "started_at_ms",
    "ended_at_ms",
    "target_concurrency",
    "peak_concurrency",
    "attempted_calls",
    "succeeded_calls",
    "failed_calls",
    "failed_timeout",
    "failed_unexpected_sip",
    "failed_transport_error",
    "failed_parse_error",
    "failed_scenario_error",
    "failed_cancelled",
    "peak_cpu_utilisation",
    "peak_memory_utilisation",
    "node_samples_in_window",
    "rtp_packets_sent",
    "rtp_packets_received",
    "calls_answered_with_inbound",
    "calls_answered_without_inbound",
    "created_at_ms",
)


def _test_row(model: LoadRunTest) -> LoadRunTestRow:
    return LoadRunTestRow(
        id=model.id,
        run_id=model.run_id,
        name=model.name,
        **{f: getattr(model, f) for f in _TEST_FIELDS},
    )


async def upsert_run(db: AsyncSession, run: LoadRunInput) -> LoadRunRow:
    """Insert or update one run, keyed on its caller-supplied id.

    Select-then-update, never a native upsert: the house rule for this schema,
    and it makes re-importing the same artifacts idempotent.
    """
    existing = await db.get(LoadRun, run.id)
    if existing is None:
        existing = LoadRun(id=run.id, created_at_ms=run.created_at_ms)
        db.add(existing)
    for name, value in asdict(run).items():
        if name == "id":
            continue
        setattr(existing, name, value)
    await db.commit()
    await db.refresh(existing)
    return _run_row(existing)


async def upsert_test(db: AsyncSession, test: LoadRunTestInput) -> LoadRunTestRow:
    """Insert or update one test step, keyed on ``(run_id, name)``."""
    stmt = select(LoadRunTest).where(
        LoadRunTest.run_id == test.run_id,  # type: ignore[arg-type]
        LoadRunTest.name == test.name,  # type: ignore[arg-type]
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is None:
        existing = LoadRunTest(
            run_id=test.run_id, name=test.name, created_at_ms=test.created_at_ms
        )
        db.add(existing)
    for name, value in asdict(test).items():
        if name in ("run_id", "name"):
            continue
        setattr(existing, name, value)
    await db.commit()
    await db.refresh(existing)
    return _test_row(existing)


async def get_run(db: AsyncSession, run_id: str) -> LoadRunRow | None:
    """One run, or None when there is no such id."""
    model = await db.get(LoadRun, run_id)
    return None if model is None else _run_row(model)


async def list_runs(
    db: AsyncSession,
    *,
    project: str | None = None,
    limit: int = DEFAULT_RUN_LIMIT,
) -> list[LoadRunRow]:
    """Runs, newest first.

    ``project`` follows the read-scope convention the sibling repositories use:
    None means every project, and a value filters to it.
    """
    stmt = select(LoadRun).order_by(
        LoadRun.created_at_ms.desc()  # type: ignore[attr-defined]
    )
    if project is not None:
        stmt = stmt.where(LoadRun.project == project)  # type: ignore[arg-type]
    stmt = stmt.limit(max(1, limit))
    return [_run_row(m) for m in (await db.execute(stmt)).scalars().all()]


async def list_tests(db: AsyncSession, run_id: str) -> list[LoadRunTestRow]:
    """One run's tests, in the order they ran.

    Ordered by ``sequence`` rather than by start time: a ramp's steps are only
    meaningful in order, and a step that never started has no start time to sort
    by.
    """
    stmt = (
        select(LoadRunTest)
        .where(LoadRunTest.run_id == run_id)  # type: ignore[arg-type]
        .order_by(
            LoadRunTest.sequence,  # type: ignore[arg-type]
            LoadRunTest.id,  # type: ignore[arg-type]
        )
    )
    return [_test_row(m) for m in (await db.execute(stmt)).scalars().all()]


__all__ = [
    "DEFAULT_PROJECT",
    "DEFAULT_RUN_LIMIT",
    "LoadRunInput",
    "LoadRunRow",
    "LoadRunTestInput",
    "LoadRunTestRow",
    "get_run",
    "list_runs",
    "list_tests",
    "upsert_run",
    "upsert_test",
]
