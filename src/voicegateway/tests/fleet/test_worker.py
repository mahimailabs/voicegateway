"""Worker presence: registration, idle/busy tracking, and the heartbeat push."""

from __future__ import annotations

import pytest

from voicegateway.fleet import worker


def _clear():
    # Stop any local-mode heartbeat thread before wiping the globals it reads.
    if worker._local_stop is not None:
        worker._local_stop.set()
    if worker._local_thread is not None:
        worker._local_thread.join(timeout=3.0)
    worker._worker = None
    worker._pusher = None
    worker._collector_url = None
    worker._api_key = None
    worker._client = None
    worker._local_enabled = False
    worker._local_db_path = None
    worker._local_thread = None
    worker._local_stop = None


@pytest.fixture(autouse=True)
def _reset():
    _clear()
    yield
    _clear()


def test_register_populates_presence(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    aid = worker.register_worker(
        "realty", project="p", tenant_id="t", collector_url="http://c", api_key="k"
    )
    assert aid == "w1"
    p = worker._worker.presence()
    assert p["agent_id"] == "w1"
    assert p["agent_name"] == "realty"
    assert p["status"] == "idle"
    assert p["active_sessions"] == 0
    assert p["project"] == "p"
    assert p["tenant_id"] == "t"
    assert p["version"]  # from _version


def test_dispatch_name_defaults_to_agent_name(monkeypatch):
    """register_worker's name is the LiveKit dispatch name by convention, so the
    presence carries it as the dispatch_name unless told otherwise."""
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    worker.register_worker("reception")
    assert worker._worker.presence()["dispatch_name"] == "reception"


def test_explicit_none_dispatch_name_is_preserved(monkeypatch):
    """A Pipecat worker has no LiveKit dispatch: an explicit None must NOT fall
    back to the agent_name, so the roster does not offer a probe it can't answer."""
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    worker.register_worker("display-label", dispatch_name=None)
    assert worker._worker.presence()["dispatch_name"] is None


def test_bump_active_toggles_status_and_clamps(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    worker.register_worker("realty")
    worker.bump_active(1)
    assert worker._worker.status == "busy"
    assert worker._worker.active_sessions == 1
    worker.bump_active(1)
    assert worker._worker.active_sessions == 2
    worker.bump_active(-1)
    worker.bump_active(-1)
    assert worker._worker.status == "idle"
    worker.bump_active(-1)  # never goes negative
    assert worker._worker.active_sessions == 0


def test_bump_active_without_worker_is_noop():
    worker.bump_active(1)
    assert worker._worker is None


async def test_push_once_posts_presence_with_bearer(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker("realty")  # no collector -> no auto-pusher started
    worker._collector_url = "http://collector"
    worker._api_key = "vk_x"
    posts: list = []

    class _FakeClient:
        async def post(self, url, json=None, headers=None):
            posts.append((url, json, headers))

    worker._client = _FakeClient()
    await worker.push_once()
    assert len(posts) == 1
    url, body, headers = posts[0]
    assert url == "http://collector/v1/agents/heartbeat"
    assert body["agent_name"] == "realty"
    assert body["status"] == "idle"
    assert headers["Authorization"] == "Bearer vk_x"


async def test_push_once_noop_without_collector(monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w1")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker("realty")
    posts: list = []

    class _FakeClient:
        async def post(self, *a, **k):
            posts.append(1)

    worker._client = _FakeClient()
    await worker.push_once()
    assert posts == []


# ---------------------------------------------------------------------------
# Local mode: thread heartbeat writing to the shared SQLite (co-located dashboard)
# ---------------------------------------------------------------------------


def _db(db_path: str):
    from voicegateway.core.config import GatewayConfig
    from voicegateway.core.database import Database

    return Database(GatewayConfig(cost_tracking={"db_path": db_path}))


async def _ensure_db(db_path: str) -> None:
    """Create the tables once up front, so the writer thread and reader never
    race on create_all."""
    conn = _db(db_path)
    try:
        await conn.create_all()
    finally:
        await conn.dispose()


async def _read_roster(db_path: str):
    import time

    from voicegateway.repository import workers_repository

    conn = _db(db_path)
    try:
        async with conn.session() as db:
            return await workers_repository.read_roster(
                db, tenant_id=None, now=time.time(), ttl_seconds=1e9
            )
    finally:
        await conn.dispose()


async def _wait_for(db_path: str, predicate, timeout: float = 5.0):
    import asyncio
    import time

    deadline = time.time() + timeout
    rows: list = []
    while time.time() < deadline:
        rows = await _read_roster(db_path)
        match = next((r for r in rows if predicate(r)), None)
        if match is not None:
            return match
        await asyncio.sleep(0.05)
    raise AssertionError(f"no matching worker within {timeout}s (rows={rows})")


def test_register_without_collector_or_local_is_silent(monkeypatch):
    """Backward compat: no collector and no local flag pushes nothing."""
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-silent")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker("agent")
    assert worker.is_registered() is True
    assert worker._local_thread is None  # no local heartbeat started
    assert worker._pusher is None


def test_register_local_starts_thread_without_event_loop(tmp_path, monkeypatch):
    """The local heartbeat is thread-based, so it starts from a plain sync call
    (a __main__ block) with no running event loop."""
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-boot")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker(
        "agent", local=True, db_path=str(tmp_path / "boot.db"), interval=0.05
    )
    assert worker._local_thread is not None
    assert worker._local_thread.is_alive()


async def test_local_heartbeat_writes_worker_row(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-local")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    db = str(tmp_path / "hb.db")
    await _ensure_db(db)
    worker.register_worker("agent", local=True, db_path=db, interval=0.05)
    row = await _wait_for(db, lambda r: r.agent_id == "w-local")
    assert row.agent_name == "agent"
    assert row.status == "idle"


async def _poll_worker_row(db_path: str, agent_id: str, timeout: float = 6.0):
    """Poll the workers table via raw sqlite until agent_id appears.

    Raw sqlite (not read_roster) so a read that races the startup migration on a
    fresh file just returns nothing that round instead of raising on a
    half-created schema."""
    import asyncio
    import sqlite3
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _table_exists(db_path, "workers"):
            con = sqlite3.connect(db_path)
            try:
                r = con.execute(
                    "SELECT agent_name, dispatch_name FROM workers WHERE agent_id=?",
                    (agent_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                r = None  # column not there yet mid-migration; try again
            finally:
                con.close()
            if r is not None:
                return r
        await asyncio.sleep(0.05)
    raise AssertionError(f"no worker row for {agent_id!r} within {timeout}s")


async def test_local_heartbeat_self_migrates_a_fresh_db(tmp_path, monkeypatch):
    """An idle agent booting against a DB with no schema (no dashboard up, no call
    yet to migrate it) must bring the file to head itself and write cleanly.

    Regression: adding workers.dispatch_name used to make such an agent fail every
    heartbeat with 'no such column' until something else ran a migration. The DB is
    deliberately NOT pre-created here (no _ensure_db), so the row only appears if
    the heartbeat thread ran the migration on startup.
    """
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-fresh")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    db = str(tmp_path / "fresh.db")
    worker.register_worker("reception", local=True, db_path=db, interval=0.05)
    agent_name, dispatch_name = await _poll_worker_row(db, "w-fresh")
    assert agent_name == "reception"
    # The column the migration added, populated: proves the schema is at head.
    assert dispatch_name == "reception"


async def test_local_heartbeat_reflects_busy_after_bump(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-busy")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    db = str(tmp_path / "busy.db")
    await _ensure_db(db)
    worker.register_worker("agent", local=True, db_path=db, interval=0.05)
    worker.bump_active(1)
    row = await _wait_for(db, lambda r: r.agent_id == "w-busy" and r.status == "busy")
    assert row.active_sessions >= 1


async def test_ensure_registered_does_not_clobber(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-idem")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    db = str(tmp_path / "idem.db")
    await _ensure_db(db)
    worker.register_worker("first", local=True, db_path=db, interval=0.05)
    # attach(heartbeat=True) path: must not overwrite the __main__ registration.
    worker.ensure_registered("second", local=True, db_path=db)
    assert worker._worker.agent_name == "first"
    row = await _wait_for(db, lambda r: r.agent_id == "w-idem")
    assert row.agent_name == "first"


def _table_exists(db_path: str, table: str) -> bool:
    import os
    import sqlite3

    if not os.path.exists(db_path):
        return False
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        return cur.fetchone() is not None
    finally:
        con.close()


async def test_local_heartbeat_migrates_stamped_not_seeded(tmp_path, monkeypatch):
    """The heartbeat brings a schema-less shared file to head via a STAMPED alembic
    upgrade, never an un-stamped create_all.

    The failure mode this guards against: create_all seeds every table with no
    alembic_version row, so a later `alembic upgrade head` (the dashboard, the
    agent's own cost sink) re-creates them and breaks the file. A real upgrade
    stamps alembic_version, so those later upgrades no-op. Proving the DB carries
    a stamped alembic_version (not just a workers table) is what separates the two.
    """
    import sqlite3

    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-migrate")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    db = str(tmp_path / "fresh.db")
    worker.register_worker("agent", local=True, db_path=db, interval=0.02)
    # Wait for the write, which only happens after the startup migration.
    await _poll_worker_row(db, "w-migrate")
    assert _table_exists(db, "workers")
    # The engine stamps a NAMED version table (alembic_version_voicegateway, so it
    # can share a file with other alembic trees). Its presence is what proves this
    # was a real upgrade, not an un-stamped create_all a later upgrade would break.
    assert _table_exists(db, "alembic_version_voicegateway")
    con = sqlite3.connect(db)
    try:
        ver = con.execute(
            "SELECT version_num FROM alembic_version_voicegateway"
        ).fetchone()
    finally:
        con.close()
    assert ver is not None and ver[0]  # a real revision, not an empty stamp


def test_collector_takes_precedence_over_local(tmp_path, monkeypatch):
    """local=True + a collector => collector wins; no local SQLite thread starts.

    This is the exact combination attach(heartbeat=True) produces in fleet mode
    (it forwards both collector_url and local=True)."""
    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-prec")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    worker.register_worker(
        "agent",
        collector_url="http://c",
        local=True,
        db_path=str(tmp_path / "prec.db"),
        interval=0.05,
    )
    assert worker._local_thread is None
    assert worker._collector_url == "http://c"


def test_attach_heartbeat_wires_ensure_registered_local(monkeypatch):
    """attach(session, heartbeat=True) forwards to ensure_registered(local=True)."""
    import voicegateway
    import voicegateway._frameworks as fw

    monkeypatch.setenv("VOICEGW_AGENT_ID", "w-attach")
    monkeypatch.delenv("VOICEGW_COLLECTOR_URL", raising=False)
    # Force the LiveKit path; spy on ensure_registered so no real DB/thread is used.
    monkeypatch.setattr(fw, "detect_framework", lambda _s: "livekit")
    calls: list = []
    monkeypatch.setattr(
        worker,
        "ensure_registered",
        lambda name, **kw: calls.append((name, kw)) or "w-attach",
    )

    class _FakeSession:
        def on(self, *_a, **_k):
            pass

    voicegateway.attach(_FakeSession(), heartbeat=True)
    assert calls, "attach(heartbeat=True) did not call ensure_registered"
    assert calls[0][1].get("local") is True
