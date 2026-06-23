"""End-to-end test of the ORM-based ApiKey stack."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voicegateway.repository.api_key_repository import ApiKeyRepository
from voicegateway.services.storage_service import StorageService
from voicegateway.services.api_key_service import ApiKeyService


@pytest.fixture
async def service(tmp_path):
    """Bootstrap schema via the legacy storage, then build the ORM stack."""
    db_path = tmp_path / "vk-orm.db"
    storage = StorageService(db_path=str(db_path))
    await storage._ensure_initialized()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    class _SessionCtx:
        def __call__(self):
            return session_factory()

    repo = ApiKeyRepository(session_factory=_SessionCtx())
    yield ApiKeyService(repository=repo)
    await engine.dispose()


async def test_create_returns_plaintext_once(service: ApiKeyService) -> None:
    created = await service.create_key(
        name="prod-bot", tenant_id="acme", issued_by="ops@vg"
    )
    assert created.plaintext.startswith("vk_")
    assert len(created.plaintext) == 35
    assert created.row.key_prefix == created.plaintext[:8]
    assert created.row.name == "prod-bot"
    assert created.row.tenant_id == "acme"
    assert created.row.revoked_at is None


async def test_verify_round_trip(service: ApiKeyService) -> None:
    created = await service.create_key(name="api-bot")
    verified = await service.verify(created.plaintext)
    assert verified is not None
    assert verified.id == created.row.id
    assert verified.name == "api-bot"


async def test_verify_rejects_wrong_plaintext(service: ApiKeyService) -> None:
    await service.create_key(name="real")
    assert await service.verify("vk_NOTAREALKEYAAAAAAAAAAAAAAAAAAAAAA") is None
    assert await service.verify("not-a-vk-token") is None


async def test_revoke_blocks_future_verify(service: ApiKeyService) -> None:
    created = await service.create_key(name="ops")
    assert await service.revoke(created.row.id) is True
    assert await service.verify(created.plaintext) is None
    # Idempotent: second revoke returns False (already revoked).
    assert await service.revoke(created.row.id) is False


async def test_list_keys_filters_revoked(service: ApiKeyService) -> None:
    a = await service.create_key(name="a")
    b = await service.create_key(name="b")
    await service.revoke(a.row.id)

    all_keys = await service.list_keys(include_revoked=True)
    active_keys = await service.list_keys(include_revoked=False)
    assert {k.id for k in all_keys} == {a.row.id, b.row.id}
    assert {k.id for k in active_keys} == {b.row.id}


async def test_mark_used_is_idempotent(service: ApiKeyService) -> None:
    created = await service.create_key(name="poller")
    assert created.row.last_used_at is None
    await service.mark_used(created.row.id)
    after = await service.get_by_id(created.row.id)
    first_stamp = after.last_used_at
    assert first_stamp is not None
    await service.mark_used(created.row.id)
    again = await service.get_by_id(created.row.id)
    assert again.last_used_at is not None
    # Time advances, so the second stamp is >=
    assert again.last_used_at >= first_stamp
