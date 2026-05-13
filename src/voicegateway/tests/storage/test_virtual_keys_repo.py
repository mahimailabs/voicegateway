"""Tests for ``virtual_keys`` (REQ-VG-TENANT-003).

Covers issuance + hash-and-verify round-trip, soft revoke (OQ5), stale
detection, and the invariant that list responses never carry the
bcrypt hash or the plaintext key.
"""

from __future__ import annotations

import pytest

from voicegateway.repository import virtual_keys as vk
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def db(tmp_path):
    storage = SQLiteStorage(db_path=str(tmp_path / "vk.db"))
    conn = await storage._ensure_initialized()
    yield conn


async def test_issuance_returns_plaintext_once(db) -> None:
    created = await vk.create_virtual_key(
        db, name="prod-bot", tenant_id="acme", issued_by="ops@vg"
    )
    assert created.plaintext.startswith("vk_")
    assert len(created.plaintext) == 35  # vk_ + 32 base32 chars
    assert created.row.key_prefix == created.plaintext[:8]
    assert created.row.name == "prod-bot"
    assert created.row.tenant_id == "acme"
    assert created.row.issued_by == "ops@vg"


async def test_verify_round_trip_returns_id_and_tenant(db) -> None:
    created = await vk.create_virtual_key(db, name="bot", tenant_id="acme")
    verified = await vk.verify(db, created.plaintext)
    assert verified is not None
    assert verified.id == created.id
    assert verified.tenant_id == "acme"
    assert verified.name == "bot"


async def test_verify_rejects_non_vk_prefix(db) -> None:
    assert await vk.verify(db, "sk-not-a-vk") is None
    assert await vk.verify(db, "") is None


async def test_verify_rejects_unknown_key(db) -> None:
    # Same prefix shape, different secret.
    assert await vk.verify(db, "vk_BADKEY" + "A" * 28) is None


async def test_verify_rejects_revoked_key(db) -> None:
    created = await vk.create_virtual_key(db, name="bot")
    assert await vk.verify(db, created.plaintext) is not None
    assert await vk.revoke(db, created.id) is True
    assert await vk.verify(db, created.plaintext) is None


async def test_revoke_is_idempotent_observable(db) -> None:
    created = await vk.create_virtual_key(db, name="bot")
    assert await vk.revoke(db, created.id) is True
    # Second revoke returns False because the row is already revoked.
    assert await vk.revoke(db, created.id) is False


async def test_revoke_keeps_row_for_audit(db) -> None:
    """OQ5: soft revoke writes revoked_at, does not delete the row."""
    created = await vk.create_virtual_key(db, name="bot")
    await vk.revoke(db, created.id)
    row = await vk.get_by_id(db, created.id)
    assert row is not None
    assert row.revoked_at is not None


async def test_mark_used_updates_last_used_at(db) -> None:
    created = await vk.create_virtual_key(db, name="bot")
    assert created.row.last_used_at is None
    await vk.mark_used(db, created.id)
    refreshed = await vk.get_by_id(db, created.id)
    assert refreshed is not None
    assert refreshed.last_used_at is not None


async def test_list_keys_excludes_plaintext_and_hash(db) -> None:
    """The VirtualKeyRow dataclass intentionally drops ``key_hash``."""
    await vk.create_virtual_key(db, name="bot1", tenant_id="acme")
    await vk.create_virtual_key(db, name="bot2")
    rows = await vk.list_keys(db)
    assert len(rows) == 2
    for row in rows:
        # Dataclass has no ``key_hash`` field.
        assert not hasattr(row, "key_hash")
        # Dataclass has no ``plaintext`` field.
        assert not hasattr(row, "plaintext")


async def test_list_keys_filter_revoked(db) -> None:
    active = await vk.create_virtual_key(db, name="active")
    revoked = await vk.create_virtual_key(db, name="revoked")
    await vk.revoke(db, revoked.id)

    all_rows = await vk.list_keys(db, include_revoked=True)
    assert {r.id for r in all_rows} == {active.id, revoked.id}

    only_active = await vk.list_keys(db, include_revoked=False)
    assert {r.id for r in only_active} == {active.id}


async def test_unscoped_key_has_null_tenant(db) -> None:
    created = await vk.create_virtual_key(db, name="unscoped")
    assert created.row.tenant_id is None
    verified = await vk.verify(db, created.plaintext)
    assert verified is not None
    assert verified.tenant_id is None


async def test_list_stale_includes_never_used_keys(db) -> None:
    """Issued-but-never-used keys past the cutoff are stale."""
    await vk.create_virtual_key(db, name="brand-new")

    # ``stale_after_days=0`` means anything issued at or before "now"
    # is considered stale, which captures the just-created row.
    stale = await vk.list_stale(db, stale_after_days=0)
    assert len(stale) == 1
    assert stale[0].name == "brand-new"


async def test_list_stale_excludes_recently_used_keys(db) -> None:
    created = await vk.create_virtual_key(db, name="busy")
    await vk.mark_used(db, created.id)

    # 365-day threshold means nothing should be stale yet.
    stale = await vk.list_stale(db, stale_after_days=365)
    assert len(stale) == 0


async def test_list_stale_excludes_revoked_keys(db) -> None:
    created = await vk.create_virtual_key(db, name="dead")
    await vk.revoke(db, created.id)
    stale = await vk.list_stale(db, stale_after_days=0)
    assert len(stale) == 0


async def test_list_stale_rejects_negative_threshold(db) -> None:
    with pytest.raises(ValueError):
        await vk.list_stale(db, stale_after_days=-1)


async def test_create_rejects_empty_name(db) -> None:
    with pytest.raises(ValueError):
        await vk.create_virtual_key(db, name="")


async def test_get_by_id_returns_none_for_missing(db) -> None:
    assert await vk.get_by_id(db, 999999) is None


async def test_two_keys_with_same_tenant_independent(db) -> None:
    a = await vk.create_virtual_key(db, name="a", tenant_id="acme")
    b = await vk.create_virtual_key(db, name="b", tenant_id="acme")
    assert a.plaintext != b.plaintext
    assert a.row.key_prefix != b.row.key_prefix or a.plaintext != b.plaintext

    # Revoking one does not affect the other.
    await vk.revoke(db, a.id)
    assert await vk.verify(db, a.plaintext) is None
    assert await vk.verify(db, b.plaintext) is not None
