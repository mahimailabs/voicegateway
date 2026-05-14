"""Tests for ``tenants`` (REQ-VG-TENANT-002)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from voicegateway.repository import tenants_repository as tr
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def db(tmp_path):
    storage = SQLiteStorage(db_path=str(tmp_path / "tenants.db"))
    await storage._ensure_initialized()
    async with storage._conn.session() as session:
        yield session


_SEED_INSERT = text(
    "INSERT INTO sessions ("
    "id, project, started_at, ended_at, modalities, "
    "total_cost_usd, request_count, tenant_id"
    ") VALUES ("
    ":id, :project, :started_at, :ended_at, :modalities, "
    ":total_cost_usd, :request_count, :tenant_id"
    ")"
)


async def _seed(db, rows):
    """Insert pre-baked session rows via the ORM session."""
    payload = [
        {
            "id": r[0],
            "project": r[1],
            "started_at": r[2],
            "ended_at": r[3],
            "modalities": r[4],
            "total_cost_usd": r[5],
            "request_count": r[6],
            "tenant_id": r[7],
        }
        for r in rows
    ]
    await db.execute(_SEED_INSERT, payload)
    await db.commit()


async def test_list_tenants_aggregates_per_tenant(db) -> None:
    await _seed(
        db,
        [
            (
                "s1",
                "default",
                "2026-05-01T10:00",
                "2026-05-01T10:05",
                "stt",
                0.5,
                3,
                "acme",
            ),
            (
                "s2",
                "default",
                "2026-05-02T11:00",
                "2026-05-02T11:02",
                "tts",
                0.1,
                1,
                "acme",
            ),
            (
                "s3",
                "default",
                "2026-05-03T09:00",
                "2026-05-03T09:30",
                "llm",
                1.25,
                5,
                "beta",
            ),
        ],
    )
    rows = await tr.list_tenants(db)
    by_tenant = {r.tenant_id: r for r in rows}
    assert by_tenant["acme"].session_count == 2
    assert by_tenant["acme"].total_cost_usd == pytest.approx(0.6)
    assert by_tenant["beta"].session_count == 1
    assert by_tenant["beta"].total_cost_usd == pytest.approx(1.25)


async def test_list_tenants_orders_by_last_seen_desc(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", "2026-05-01T10:05", "x", 0.0, 0, "older"),
            ("s2", "p", "2026-05-03T09:00", "2026-05-03T09:30", "x", 0.0, 0, "newest"),
            ("s3", "p", "2026-05-02T11:00", "2026-05-02T11:02", "x", 0.0, 0, "middle"),
        ],
    )
    rows = await tr.list_tenants(db)
    assert [r.tenant_id for r in rows] == ["newest", "middle", "older"]


async def test_list_tenants_excludes_unattributed_bucket(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, None),
            ("s2", "p", "2026-05-02T11:00", None, "x", 0.0, 0, "acme"),
        ],
    )
    rows = await tr.list_tenants(db)
    assert [r.tenant_id for r in rows] == ["acme"]


async def test_list_tenants_substring_query(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, "acme-prod"),
            ("s2", "p", "2026-05-02T10:00", None, "x", 0.0, 0, "acme-staging"),
            ("s3", "p", "2026-05-03T10:00", None, "x", 0.0, 0, "beta-prod"),
        ],
    )
    acme_only = await tr.list_tenants(db, query="acme")
    assert {r.tenant_id for r in acme_only} == {"acme-prod", "acme-staging"}

    prod_only = await tr.list_tenants(db, query="prod")
    assert {r.tenant_id for r in prod_only} == {"acme-prod", "beta-prod"}


async def test_list_tenants_query_escapes_percent_literal(db) -> None:
    """A user typing '%' should NOT match every tenant (no wildcard expansion)."""
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, "acme"),
            ("s2", "p", "2026-05-02T10:00", None, "x", 0.0, 0, "beta"),
        ],
    )
    rows = await tr.list_tenants(db, query="%")
    assert rows == []


async def test_list_tenants_query_escapes_underscore_literal(db) -> None:
    """'_' should NOT match any single character."""
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, "ab"),
            ("s2", "p", "2026-05-02T10:00", None, "x", 0.0, 0, "ac"),
            ("s3", "p", "2026-05-03T10:00", None, "x", 0.0, 0, "a_c"),
        ],
    )
    rows = await tr.list_tenants(db, query="_")
    assert {r.tenant_id for r in rows} == {"a_c"}


async def test_list_tenants_limit_clamped(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, "t1"),
            ("s2", "p", "2026-05-02T10:00", None, "x", 0.0, 0, "t2"),
            ("s3", "p", "2026-05-03T10:00", None, "x", 0.0, 0, "t3"),
        ],
    )
    rows = await tr.list_tenants(db, limit=2)
    assert len(rows) == 2

    # Zero or negative clamps to 1.
    rows = await tr.list_tenants(db, limit=0)
    assert len(rows) == 1


async def test_get_tenant_returns_single_row(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", "2026-05-01T10:05", "x", 0.5, 3, "acme"),
            ("s2", "p", "2026-05-02T11:00", "2026-05-02T11:02", "x", 0.1, 1, "acme"),
        ],
    )
    row = await tr.get_tenant(db, "acme")
    assert row is not None
    assert row.session_count == 2
    assert row.total_cost_usd == pytest.approx(0.6)


async def test_get_tenant_returns_none_when_unseen(db) -> None:
    assert await tr.get_tenant(db, "missing") is None


async def test_count_tenants(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", None, "x", 0.0, 0, "acme"),
            ("s2", "p", "2026-05-02T10:00", None, "x", 0.0, 0, "beta"),
            ("s3", "p", "2026-05-03T10:00", None, "x", 0.0, 0, "acme"),  # dup
            ("s4", "p", "2026-05-04T10:00", None, "x", 0.0, 0, None),  # unattributed
        ],
    )
    assert await tr.count_tenants(db) == 2


async def test_get_unattributed_aggregates(db) -> None:
    await _seed(
        db,
        [
            ("s1", "p", "2026-05-01T10:00", "2026-05-01T10:05", "x", 0.05, 1, None),
            ("s2", "p", "2026-05-02T11:00", None, "x", 0.02, 1, None),
            ("s3", "p", "2026-05-03T11:00", None, "x", 0.10, 1, "acme"),
        ],
    )
    u = await tr.get_unattributed_aggregates(db)
    assert u.session_count == 2
    assert u.total_cost_usd == pytest.approx(0.07)


async def test_get_unattributed_aggregates_zero_defaults(db) -> None:
    """Empty bucket returns sane defaults (session_count=0, cost=0.0)."""
    u = await tr.get_unattributed_aggregates(db)
    assert u.session_count == 0
    assert u.total_cost_usd == 0.0
    assert u.first_seen is None
    assert u.last_seen is None
