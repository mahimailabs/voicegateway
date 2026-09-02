"""get_session is how a route gets a database session. Nothing else is.

Twenty-four handlers currently open their own via ``storage._conn.session()``,
each re-solving "how do I reach the database" and each a place where a tenant
guard could have gone and did not. This dependency is the single seam those
migrate onto.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.server.api._deps import get_gateway, get_session
from voicegateway.tests.server._telemetry_harness import _Harness


@pytest.fixture
async def harness():
    h = _Harness()
    try:
        yield h
    finally:
        h.cleanup()


async def test_route_receives_a_working_session(harness):
    app: FastAPI = harness.app

    @app.get("/_probe")
    async def probe(session: AsyncSession = Depends(get_session)) -> dict:
        one = (await session.execute(text("SELECT 1"))).scalar_one()
        return {"one": one}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/_probe")

    assert response.status_code == 200
    assert response.json() == {"one": 1}


async def test_session_has_migrations_already_run(harness):
    """A route must never be handed a session over an unmigrated database."""
    app: FastAPI = harness.app

    @app.get("/_probe_migrated")
    async def probe(session: AsyncSession = Depends(get_session)) -> dict:
        count = (
            await session.execute(text("SELECT count(*) FROM api_keys"))
        ).scalar_one()
        return {"api_keys": count}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/_probe_migrated")

    assert response.status_code == 200
    assert response.json() == {"api_keys": 0}


async def test_get_session_503_when_storage_disabled(harness):
    """Storage off is a 503, not an AttributeError halfway through a handler.

    ``Gateway.storage`` is a read-only property returning ``None`` when cost
    tracking is disabled, so the dependency is overridden rather than the
    Gateway mutated. That also keeps the test on ``get_session``'s own
    contract instead of on how a Gateway decides it has no storage.
    """
    app: FastAPI = harness.app

    class _NoStorageGateway:
        storage = None

    app.dependency_overrides[get_gateway] = lambda: _NoStorageGateway()

    @app.get("/_probe_disabled")
    async def probe(session: AsyncSession = Depends(get_session)) -> dict:
        return {}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/_probe_disabled")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "storage is disabled" in response.json()["detail"]


def test_deps_module_no_longer_reaches_into_storage():
    """The module that defines the boundary must not be violating it."""
    import re
    from pathlib import Path

    import voicegateway.server.api._deps as deps

    source = Path(deps.__file__).read_text(encoding="utf-8")
    offenders = [
        f"{lineno}: {line.strip()}"
        for lineno, line in enumerate(source.splitlines(), 1)
        if re.search(r"\._conn\b|_ensure_initialized\(\)", line)
    ]
    assert not offenders, offenders
