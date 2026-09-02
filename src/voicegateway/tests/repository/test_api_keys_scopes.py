"""Keys are minted with explicit scopes. The wildcard is not mintable.

VG-SEC-006: every key the product minted defaulted to ``*``, and the scope
check short-circuits on ``*``. Scope enforcement ran on every request and
always passed, which is worse than not running: the matrix, the docs and the
key list all described a system of scopes that decided nothing.

The gap closes at the mint, not at the check. Refusing ``*`` in
``has_scope`` would have locked out every key already issued; refusing it at
``create_api_key`` means no NEW inert key can be made while the existing ones
keep working, loudly, until 0.27.0 withdraws them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from voicegateway.repository import api_keys_repository as api_keys
from voicegateway.tests.server._telemetry_harness import _Harness


@pytest.fixture
async def session():
    harness = _Harness()
    try:
        async with harness.gateway.storage.session() as db:
            yield db
    finally:
        harness.cleanup()


async def test_scopes_is_required():
    """No default. A caller that forgets scopes gets a TypeError, not a ``*``."""
    with pytest.raises(TypeError):
        await api_keys.create_api_key(None, name="k")  # type: ignore[call-arg]


async def test_wildcard_is_refused_at_mint(session):
    """Both spellings: the bare wildcard and one hidden in a list."""
    with pytest.raises(ValueError, match="wildcard"):
        await api_keys.create_api_key(session, name="k", scopes="*")
    with pytest.raises(ValueError, match="wildcard"):
        await api_keys.create_api_key(session, name="k", scopes="read,*")


async def test_unknown_scope_is_refused(session):
    """A typo must not mint a key that silently authorizes nothing."""
    with pytest.raises(ValueError, match="unknown scope"):
        await api_keys.create_api_key(session, name="k", scopes="root")


async def test_empty_scopes_is_refused(session):
    """An empty string is a caller bug, not a request for zero scopes."""
    with pytest.raises(ValueError, match="at least one"):
        await api_keys.create_api_key(session, name="k", scopes="  ,  ")


async def test_scopes_are_normalized(session):
    """Whitespace and order are not part of a key's identity."""
    created = await api_keys.create_api_key(
        session, name="k", scopes=" write , ingest ,ingest"
    )
    verified = await api_keys.verify(session, created.plaintext)
    assert verified is not None
    assert verified.scopes == "ingest,write"


async def test_explicit_scopes_round_trip(session):
    """The happy path: an ingest key verifies and covers only ingest."""
    created = await api_keys.create_api_key(session, name="agent", scopes="ingest")
    verified = await api_keys.verify(session, created.plaintext)
    assert verified is not None
    assert verified.has_scope("ingest", enforce=True)
    assert not verified.has_scope("admin", enforce=True)


async def test_audit_lists_legacy_wildcard_keys(session):
    """A key minted before 0.26.0, written directly the way the old mint did."""
    await session.execute(
        text(
            "INSERT INTO api_keys (key_prefix, key_hash, name, role, scopes, "
            "issued_at) VALUES ('vk_old', 'h', 'legacy', 'tenant', '*', "
            "CURRENT_TIMESTAMP)"
        )
    )
    await session.execute(
        text(
            "INSERT INTO api_keys (key_prefix, key_hash, name, role, scopes, "
            "issued_at) VALUES ('vk_mix', 'h', 'mixed', 'tenant', 'read,*', "
            "CURRENT_TIMESTAMP)"
        )
    )
    await api_keys.create_api_key(session, name="modern", scopes="read")
    found = await api_keys.list_wildcard_keys(session)
    assert sorted(k.name for k in found) == ["legacy", "mixed"]


async def test_audit_skips_revoked_wildcard_keys(session):
    """A revoked key needs no re-minting, so it is not a finding."""
    await session.execute(
        text(
            "INSERT INTO api_keys (key_prefix, key_hash, name, role, scopes, "
            "issued_at, revoked_at) VALUES ('vk_rev', 'h', 'gone', 'tenant', "
            "'*', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    await session.commit()
    assert await api_keys.list_wildcard_keys(session) == []
