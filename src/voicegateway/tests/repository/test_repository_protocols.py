"""The repository Protocols name contracts a storage backend must satisfy.

``TraceRepository`` is the one Wave 1 needs: Task 20's spans repository is
written against it from its first commit, so the trace tables are born behind
an interface rather than having one retrofitted in Wave 3.

``RequestReadRepository`` is deliberately absent. The spec assumed
``clickhouse/read_repository.py`` duplicated one SQL module's read surface, so
one Protocol could name what both satisfy. Measured, that is false: of its
nine read functions, seven have same-named SQL counterparts spread across five
modules (cost, latency, request_log, session, billing) and two
(``get_session_requests``, ``get_cost_by_tenant_admin``) have none. No single
SQL module implements the surface, so there is nothing honest for a
``runtime_checkable`` assertion to bind to. See the Wave 1 report for the
options; Wave 3 owns putting ClickHouse behind an interface.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.repository import protocols
from voicegateway.schemas.telemetry.security_schema import PrincipalKind
from voicegateway.server.api._deps import Principal
from voicegateway.telemetry.trace_schema import SpanRecord


def test_principal_defaults_are_unrestricted_operator():
    """Every existing key keeps working: no allowlist means no restriction."""
    principal = Principal(tenant_id=None, is_admin=True, api_key_id=None)
    assert principal.project_ids is None
    assert principal.kind is PrincipalKind.OPERATOR


def test_principal_carries_a_project_allowlist_and_a_kind():
    principal = Principal(
        tenant_id="acme",
        is_admin=False,
        api_key_id=7,
        project_ids=frozenset({"p1"}),
        kind=PrincipalKind.TENANT_KEY,
    )
    assert principal.project_ids == frozenset({"p1"})
    assert principal.kind is PrincipalKind.TENANT_KEY


def test_principal_is_still_frozen():
    """The principal is decided once per request and never edited after."""
    import dataclasses

    principal = Principal(tenant_id="acme", is_admin=False, api_key_id=1)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        principal.tenant_id = "beta"  # type: ignore[misc]


class _StubTraceRepository:
    """Shape check only. Task 20 ships the real one."""

    async def upsert_spans(
        self, session: AsyncSession, records: list[SpanRecord], *, tenant_id: str
    ) -> int:
        return len(records)

    async def get_trace(
        self, session: AsyncSession, trace_id: str, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        return []

    async def list_spans_by_session(
        self, session: AsyncSession, session_id: str, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        return []

    async def list_events(
        self, session: AsyncSession, span_row_id: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        return []

    async def list_links(
        self, session: AsyncSession, span_row_id: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        return []


def test_trace_repository_protocol_is_satisfiable():
    """A conforming implementation exists, so the Protocol is not aspirational."""
    assert isinstance(_StubTraceRepository(), protocols.TraceRepository)


def test_trace_repository_requires_every_read_to_be_tenant_scoped():
    """Every method takes tenant_id as a required keyword. That is the point."""
    import inspect

    for name in (
        "upsert_spans",
        "get_trace",
        "list_spans_by_session",
        "list_events",
        "list_links",
    ):
        signature = inspect.signature(getattr(protocols.TraceRepository, name))
        tenant = signature.parameters.get("tenant_id")
        assert tenant is not None, f"{name} takes no tenant_id"
        assert tenant.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert tenant.default is inspect.Parameter.empty, (
            f"{name} tenant_id is optional"
        )
