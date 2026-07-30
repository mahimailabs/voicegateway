"""ORM model for the ``diagnostics_runs`` table.

One row per LiveKit diagnostics run started from the dashboard. Before this
table the runs lived in a process-local dict trimmed to the newest 20 entries,
so every restart erased the history and a run that outlived its process left
nothing behind at all -- while the runs it recorded had placed real, billed
calls. A run is the most expensive thing this dashboard can do; it should not be
the least durable.

The three timestamp columns hold the **ISO-8601 strings the endpoint already
returns**, verbatim, not epoch numbers. That is deliberate: the
``/api/diagnostics/runs`` payload must stay byte-identical, and re-formatting a
timestamp through epoch seconds and back would change the exact string the
dashboard renders. Retention compares them lexically, which is sound for
same-format UTC ISO-8601 and is what the session pass already does.

``checks``, ``config`` and ``results`` are JSON text: they are opaque probe
payloads that this layer stores and returns unchanged. Nothing here normalises,
repairs or re-derives a probe result -- notably there is no loss column (the SFU
probe hardcodes ``loss_pct`` and a stored 0.0 would read as a measurement) and
no percentile column (percentiles belong to the presentation layer, which has to
render "max of N" below 10 samples).
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import Index, Text
from sqlmodel import Field, SQLModel


class DiagnosticsRun(SQLModel, table=True):
    """One diagnostics run: what was asked for, what came back, and when."""

    __tablename__: ClassVar[str] = "diagnostics_runs"
    __table_args__ = (
        # Newest-first history reads, and the retention scan.
        Index("idx_diagnostics_runs_created_at", "created_at"),
        # The per-project retention pass.
        Index("idx_diagnostics_runs_project", "project"),
    )

    # uuid4 hex minted by the endpoint. NOT NULL and the primary key, so this is
    # the one upsert in the schema where a native ON CONFLICT would in fact be
    # safe; the repository still uses select-then-update to match every other
    # repository (and to behave identically on SQLite and PostgreSQL).
    run_id: str = Field(primary_key=True)

    # A diagnostics run is host-scoped: it probes a LiveKit deployment, not a
    # project, and the request body carries no project. The column exists so the
    # run ages out on the same per-project retention pass as everything else,
    # and it is not part of the endpoint payload.
    project: str = Field(default="default")

    # 'queued' | 'running' | 'done' | 'failed', as last observed. A run
    # interrupted by a restart stays at its last observed status: nothing here
    # rewrites history to guess how it ended.
    status: str = Field(default="queued")

    # JSON arrays/objects, stored exactly as the endpoint serves them. Text, not
    # VARCHAR: these hold serialized documents of no fixed length (a full probe
    # payload), and the migration declares them that way -- nothing in this repo
    # runs autogenerate, so the model has to agree with the DDL by hand.
    checks_json: str = Field(sa_type=Text)
    config_json: str = Field(sa_type=Text)
    # NULL until a run produces results. NULL means "no results were recorded",
    # which is a different claim from "{}" (an empty result set).
    results_json: str | None = Field(default=None, sa_type=Text)

    verdict: str | None = None
    error: str | None = None

    # ISO-8601 UTC strings (see the module docstring).
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
