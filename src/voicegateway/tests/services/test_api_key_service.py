"""End-to-end test of ApiKeyService over the api-keys repository."""

from __future__ import annotations

import pytest

from voicegateway.services.api_key_service import ApiKeyService
from voicegateway.services.storage_service import StorageService


@pytest.fixture
async def service(tmp_path):
    """The service over a real, migrated SQLite store, as the container wires it."""
    storage = StorageService(db_path=str(tmp_path / "vk.db"))
    yield ApiKeyService(session_factory=storage.session)
    await storage.aclose()


async def test_create_returns_plaintext_once(service: ApiKeyService) -> None:
    created = await service.create_key(
        name="prod-bot", scopes="read", tenant_id="acme", issued_by="ops@vg"
    )
    assert created.plaintext.startswith("vk_")
    assert len(created.plaintext) == 35
    assert created.row.key_prefix == created.plaintext[:8]
    assert created.row.name == "prod-bot"
    assert created.row.tenant_id == "acme"
    assert created.row.revoked_at is None


async def test_verify_round_trip(service: ApiKeyService) -> None:
    created = await service.create_key(name="api-bot", scopes="read")
    verified = await service.verify(created.plaintext)
    assert verified is not None
    assert verified.id == created.row.id
    assert verified.name == "api-bot"


async def test_verify_rejects_wrong_plaintext(service: ApiKeyService) -> None:
    await service.create_key(name="real", scopes="read")
    assert await service.verify("vk_NOTAREALKEYAAAAAAAAAAAAAAAAAAAAAA") is None
    assert await service.verify("not-a-vk-token") is None


async def test_revoke_blocks_future_verify(service: ApiKeyService) -> None:
    created = await service.create_key(name="ops", scopes="read")
    assert await service.revoke(created.row.id) is True
    assert await service.verify(created.plaintext) is None
    # Idempotent: second revoke returns False (already revoked).
    assert await service.revoke(created.row.id) is False


async def test_list_keys_filters_revoked(service: ApiKeyService) -> None:
    a = await service.create_key(name="a", scopes="read")
    b = await service.create_key(name="b", scopes="read")
    await service.revoke(a.row.id)

    all_keys = await service.list_keys(include_revoked=True)
    active_keys = await service.list_keys(include_revoked=False)
    assert {k.id for k in all_keys} == {a.row.id, b.row.id}
    assert {k.id for k in active_keys} == {b.row.id}


async def test_mark_used_is_idempotent(service: ApiKeyService) -> None:
    created = await service.create_key(name="poller", scopes="read")
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


async def test_create_refuses_the_wildcard(service: ApiKeyService) -> None:
    """VG-SEC-006 at the service layer, not only behind the route.

    This is the door the ORM model sat behind: ``ApiKey.scopes`` defaults to
    ``"*"``, and before 0.26.0 this method never set the field, so the default
    applied. Asserting here rather than only through the endpoint pins the
    layer that actually held the bug.
    """
    with pytest.raises(ValueError, match="wildcard"):
        await service.create_key(name="wild", scopes="*")
