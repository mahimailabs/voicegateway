"""managed_rate_rules repository: scope-keyed upsert, list, delete, validation."""

from __future__ import annotations

import pytest

from voicegateway.core.config import GatewayConfig
from voicegateway.core.database import Database
from voicegateway.repository import managed_rate_rule_repository as repo


async def _db(tmp_path) -> Database:
    db = Database(GatewayConfig(cost_tracking={"db_path": str(tmp_path / "rules.db")}))
    await db.run_migrations()
    return db


def test_scope_key_is_deterministic() -> None:
    a = repo.scope_key(
        modality="stt", provider="deepgram", model="nova-3", tenant="acme", plan=None
    )
    b = repo.scope_key(
        modality="stt", provider="deepgram", model="nova-3", tenant="acme", plan=None
    )
    assert a == b == "acme|*|stt|deepgram|nova-3"


def test_validate_rule_kinds() -> None:
    assert repo.validate_rule(markup=1.3, fixed=None, unit=None) == "cost_plus"
    assert repo.validate_rule(markup=None, fixed=0.006, unit="minute") == "fixed"
    with pytest.raises(ValueError):
        repo.validate_rule(markup=1.3, fixed=0.006, unit="minute")  # both
    with pytest.raises(ValueError):
        repo.validate_rule(markup=None, fixed=0.006, unit="furlong")  # bad unit
    with pytest.raises(ValueError):
        repo.validate_rule(markup=None, fixed=None, unit=None)  # neither


async def test_upsert_list_delete(tmp_path) -> None:
    db = await _db(tmp_path)
    try:
        async with db.session() as s:
            rid = await repo.upsert_rule(s, provider="openai", markup=1.5)
        async with db.session() as s:
            rows = await repo.list_rules(s)
        assert len(rows) == 1
        assert rows[0]["rule_id"] == rid
        assert rows[0]["provider"] == "openai"
        assert rows[0]["kind"] == "cost_plus"
        assert rows[0]["markup"] == pytest.approx(1.5)

        # Upsert the same scope updates in place (no duplicate row).
        async with db.session() as s:
            rid2 = await repo.upsert_rule(s, provider="openai", markup=2.0)
        assert rid2 == rid
        async with db.session() as s:
            rows = await repo.list_rules(s)
        assert len(rows) == 1
        assert rows[0]["markup"] == pytest.approx(2.0)

        # Delete.
        async with db.session() as s:
            removed = await repo.delete_rule(s, rid)
        assert removed is True
        async with db.session() as s:
            assert await repo.list_rules(s) == []
    finally:
        await db.dispose()


async def test_upsert_fixed_rule_persists_unit(tmp_path) -> None:
    db = await _db(tmp_path)
    try:
        async with db.session() as s:
            await repo.upsert_rule(
                s,
                modality="stt",
                provider="deepgram",
                model="nova-3",
                fixed=0.006,
                unit="minute",
            )
        async with db.session() as s:
            rows = await repo.list_rules(s)
        assert rows[0]["kind"] == "fixed"
        assert rows[0]["unit_price_usd"] == pytest.approx(0.006)
        assert rows[0]["unit"] == "minute"
        assert rows[0]["markup"] is None
    finally:
        await db.dispose()
