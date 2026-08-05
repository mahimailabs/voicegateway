"""A load-test run and its per-test rows.

``diagnostics_runs`` models one VoiceGateway probe: a handful of checks against a
deployment, executed by this process. A load run is a different shape entirely.
It is executed by an EXTERNAL generator, it produces one row per test step rather
than one per check, and the numbers that matter are counts of calls somebody else
placed. Forcing it into ``diagnostics_runs`` would mean a run whose ``results``
blob has to be parsed to answer "what concurrency did test three reach", which is
the question the whole table exists to answer.

**Every measured column is nullable and NULL means "not measured".** Nothing here
is created NOT NULL with a 0 default. A 0 in ``peak_concurrency`` describes a
test that carried no calls, which is a different claim from "the artifact did not
say", and the second one has to stay distinguishable or a report will present a
missing figure as a measured one.

**Nothing derived is stored.** ``load_run_tests`` holds attempted and succeeded
counts, not an establishment rate: a stored rate is a number that can disagree
with the counts beside it after one bad import, and the gate that judges it wants
the counts anyway. The same reasoning keeps the generator's own reported ratio
out of this table entirely, because reading a generator's self-assessment back as
the verdict is a run grading its own homework.

**Provenance is derived from evidence, not asserted.** ``artifact_sha256`` is the
checksum of the artifact a row was imported from, and a report calls itself
``measured`` only when one is present. A boolean flag here would be a claim a
future writer could set without holding the artifact.

RTP counts on ``load_run_tests`` are PER-TEST AGGREGATES, which is a different
thing from the per-leg RTP figures ``call_leg_model`` deliberately does not
carry. That absence is about quality attributed to one participant's leg, none of
which is observable server-side. A total of packets a load generator's own
endpoints sent and received across a test is observed by those endpoints and is
the evidence that media flowed in both directions.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class LoadRun(SQLModel, table=True):
    """One execution of a load plan: a ramp, a soak, a single step."""

    __tablename__: ClassVar[str] = "load_runs"
    __table_args__ = (
        # Retention ages by creation, and the run list reads newest first.
        Index("idx_load_runs_created_at_ms", "created_at_ms"),
        Index("idx_load_runs_project", "project"),
    )

    # Caller-supplied, so one run's calls can be found by the same id an
    # observation was stamped with. Not a surrogate integer for that reason.
    id: str = Field(primary_key=True)
    project: str = Field(default="default")
    label: str | None = None

    # Which generator produced the artifacts, and which build of it. Stored as
    # the tool reported them rather than as anything this repo assumes: a
    # capacity number is only reproducible against a named version.
    tool: str | None = None
    tool_version: str | None = None
    # The generator's own summary schema version, so a parser can refuse a shape
    # it has not seen instead of silently misreading it.
    artifact_schema_version: str | None = None

    started_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    ended_at_ms: int | None = Field(default=None, sa_type=BigInteger)

    # Presence of a checksum is what makes a report call itself measured. A row
    # with none was built from fixtures, and everything derived from it is
    # synthetic.
    artifact_sha256: str | None = None
    # Where the artifacts were read from, for a human retracing the import.
    artifact_source: str | None = Field(default=None, sa_type=Text)

    notes: str | None = Field(default=None, sa_type=Text)
    created_at_ms: int = Field(sa_type=BigInteger)


class LoadRunTest(SQLModel, table=True):
    """One test step within a run: the row an operator is owed per test."""

    __tablename__: ClassVar[str] = "load_run_tests"
    __table_args__ = (
        # Re-importing the same artifacts must update rather than duplicate.
        # Both columns are NOT NULL, so this constraint has no nullable-key
        # problem; the repository still reads before writing, which is the house
        # rule for every upsert here.
        UniqueConstraint("run_id", "name", name="uq_load_run_tests_run_name"),
        # The read path: one run's tests in the order they ran.
        Index("idx_load_run_tests_run_id_sequence", "run_id", "sequence"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    name: str
    # Order within the run. A ramp's steps are only meaningful in sequence, and
    # sorting by start time breaks for steps that never started.
    sequence: int = Field(default=0)

    started_at_ms: int | None = Field(default=None, sa_type=BigInteger)
    ended_at_ms: int | None = Field(default=None, sa_type=BigInteger)

    # What the plan asked for, versus what was actually reached. Both are kept:
    # a ramp step that asked for 200 and plateaued at 124 is the single most
    # important thing a capacity report can say, and one column cannot say it.
    target_concurrency: int | None = None
    peak_concurrency: int | None = None

    # Counts, never a rate. See the module docstring.
    attempted_calls: int | None = None
    succeeded_calls: int | None = None
    failed_calls: int | None = None

    # Failures broken down by cause, which is what item 62 asks for. A total
    # with no breakdown cannot tell a timeout from a rejected INVITE, and those
    # point at different halves of the deployment.
    failed_timeout: int | None = None
    failed_unexpected_sip: int | None = None
    failed_transport_error: int | None = None
    failed_parse_error: int | None = None
    failed_scenario_error: int | None = None
    failed_cancelled: int | None = None

    # Correlated from node_samples over this test's window, by DATA4. Fractions
    # in 0..1, not percentages, matching what the gates compare against.
    peak_cpu_utilisation: float | None = None
    peak_memory_utilisation: float | None = None
    # How many node samples the window actually contained, so a peak over three
    # samples is never presented as if it came from three hundred.
    node_samples_in_window: int | None = None

    # Per-test totals across the generator's own endpoints. Evidence that media
    # flowed both ways; see the module docstring for why this is not the per-leg
    # RTP that call_legs deliberately omits.
    rtp_packets_sent: int | None = Field(default=None, sa_type=BigInteger)
    rtp_packets_received: int | None = Field(default=None, sa_type=BigInteger)

    # How many answered calls received audio back, and how many received none.
    # PER-CALL counts, which is the whole point: the RTP totals above cannot
    # distinguish a test where every call carried audio from one where half
    # carried none and half carried double. NULL means the per-call records
    # were absent or of an unmapped schema, and is never read as zero.
    calls_answered_with_inbound: int | None = None
    calls_answered_without_inbound: int | None = None

    created_at_ms: int = Field(sa_type=BigInteger)
