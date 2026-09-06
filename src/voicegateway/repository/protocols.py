"""Repository contracts, as ``typing.Protocol``.

A module or object satisfies a Protocol structurally: it exposes the named
methods. ``runtime_checkable`` lets a test assert conformance with
``isinstance``, which checks attribute presence; mypy checks the signatures
when a value is annotated with the Protocol type.

Every method takes ``*, tenant_id`` as a required keyword. There is no
fallback to a ContextVar and no fallback to a field on the row. That
signature is VG-SEC-001 made structural: the defect was a writer preferring
the payload's tenant over the caller's, which cannot be expressed when the
caller's tenant is the only tenant the function accepts.

Not here yet: a read-side Protocol over ``clickhouse/read_repository.py``.
The spec assumed that module duplicated one SQL module's surface. Measured,
its nine read functions map to five different SQL modules and two have no SQL
counterpart at all, so no single module satisfies a combined Protocol. Wave 3
owns putting ClickHouse behind an interface, and the shape of that interface
is a decision for whoever does it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.telemetry.trace_schema import SpanRecord


@runtime_checkable
class TraceRepository(Protocol):
    """Spans, events and links, tenant-scoped on every call.

    Task 20's ``spans_repository`` is written against this from its first
    commit, so the trace tables never exist in a state where a read could
    forget to filter by tenant.
    """

    async def upsert_spans(
        self, session: AsyncSession, records: list[SpanRecord], *, tenant_id: str
    ) -> int:
        """Insert or update spans under ``tenant_id``, returning how many landed.

        ``tenant_id`` wins over anything the record carries. A ``SpanRecord``
        has a ``correlation.tenant_id`` field that an untrusted payload can
        set, and a receiver that stored it would be VG-SEC-001 again on a new
        route (VG-SEC-015).
        """
        ...

    async def get_trace(
        self, session: AsyncSession, trace_id: str, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Return every span of one trace, filtered to ``tenant_id``."""
        ...

    async def list_spans_by_session(
        self, session: AsyncSession, session_id: str, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Return the spans of one voice session, filtered to ``tenant_id``."""
        ...

    async def list_events(
        self, session: AsyncSession, span_row_id: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Return one span's events. Tenant-scoped so a row id cannot leak."""
        ...

    async def list_links(
        self, session: AsyncSession, span_row_id: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Return one span's links. Tenant-scoped so a row id cannot leak."""
        ...


__all__ = ["TraceRepository"]
