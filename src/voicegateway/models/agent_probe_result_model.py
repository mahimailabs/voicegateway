"""ORM model for the ``agent_probe_results`` table.

One row per agent: the latest probe result, cached so the Agents page can show a
per-agent latency graph without re-running a (billed) probe on every view. A
probe writes the row; the page reads it and only runs a new one on demand.
"""

from __future__ import annotations

from typing import ClassVar

from sqlmodel import Field, SQLModel


class AgentProbeResult(SQLModel, table=True):
    """The last cached probe result for one agent, keyed by ``agent_id``."""

    __tablename__: ClassVar[str] = "agent_probe_results"

    # One cached result per agent (single-tenant OSS). A new probe overwrites it.
    agent_id: str = Field(primary_key=True)
    # The probe response dict, JSON-serialized (e2e, components, cost, models...).
    result_json: str
    # Epoch seconds the probe ran, so the UI can show "measured Ns ago".
    created_at: float
