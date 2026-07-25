"""Process-level worker presence for the fleet roster.

The LiveKit server API lists agents that are IN a room, but not the idle/registered
worker pool, so ``voicegw livekit agents`` can only ever show busy agents. This
closes that gap from the agent side: a worker calls :func:`register_worker` once at
boot, and the process then heartbeats a periodic presence carrying an idle/busy
status that ``voicegateway.attach`` keeps accurate (bumping an active-session
counter on each session open/close), plus the version, project, host, and region
the LiveKit server never knows.

Two heartbeat transports, chosen by whether a collector is configured:

- **Collector mode** (``collector_url`` / ``VOICEGW_COLLECTOR_URL`` set): an asyncio
  task pushes to the collector's ``/v1/agents/heartbeat``. Needs a running event
  loop (starts on the first ``bump_active`` in an async context if registered early).
- **Local mode** (no collector): a background daemon thread writes the presence row
  straight to the shared SQLite (``VOICEGW_DB_PATH`` / the default), which the
  co-located dashboard reads. The thread needs no event loop, so a worker
  registered at ``__main__`` boot is visible in the roster while idle, immediately.

Best-effort by design: it never blocks or breaks the agent. A failed push/write is
swallowed.

One writer per agent identity. In the LiveKit process-executor model (``agent
dev``/``start``) the worker is the MAIN process and per-call work runs in spawned
JOB SUBPROCESSES, so register the worker once in your ``__main__`` block (the main
process) with ``register_worker("agent", local=True)`` and do NOT also pass
``attach(heartbeat=True)`` there: the subprocess would become a second writer of the
same ``(tenant=None, agent_id)`` row, which the get-then-insert upsert cannot
coalesce. ``attach(heartbeat=True)`` is for SINGLE-process agents (Pipecat / the
LiveKit thread executor), where attach runs in the one process and is the sole
writer.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Milliseconds is overkill for presence; a quarter-minute keeps the roster fresh
# without hammering the collector. The read side ages a worker out after a small
# multiple of this (see the /v1/agents TTL).
_DEFAULT_INTERVAL = 15.0

# Kept in sync with core.database.DEFAULT_DB_PATH; defined here so importing this
# module (which attach imports) stays free of the SQLAlchemy import chain.
_DEFAULT_DB_PATH = "~/.config/voicegateway/voicegw.db"


# Sentinel for register_worker(dispatch_name=...): "caller said nothing", which
# defaults the dispatch name to agent_name (register_worker's name IS the LiveKit
# dispatch name by convention). Passing an explicit None means "this worker has
# no dispatch name" (a Pipecat agent), which must NOT fall back to agent_name.
class _Unset:
    pass


_UNSET = _Unset()


@dataclass
class _Worker:
    agent_id: str
    agent_name: str
    version: str
    project: str
    tenant_id: str | None
    region: str | None
    host: str
    started_at: float
    # The LiveKit agent_name this worker dispatches under, or None when it has no
    # LiveKit dispatch (Pipecat). Distinct from agent_name (the display label).
    dispatch_name: str | None = None
    active_sessions: int = 0

    @property
    def status(self) -> str:
        return "busy" if self.active_sessions > 0 else "idle"

    def presence(self) -> dict[str, Any]:
        from voicegateway.fleet.resource import sample_memory

        rss, total = sample_memory()
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "dispatch_name": self.dispatch_name,
            "status": self.status,
            "active_sessions": self.active_sessions,
            "version": self.version,
            "project": self.project,
            "tenant_id": self.tenant_id,
            "region": self.region,
            "host": self.host,
            "started_at": self.started_at,
            "memory_rss_bytes": rss,
            "memory_total_bytes": total,
            "ts": time.time(),
        }


# Process-wide singletons. One worker per process (a worker registers one agent).
_worker: _Worker | None = None
_pusher: asyncio.Task | None = None  # collector-mode asyncio task
_collector_url: str | None = None
_api_key: str | None = None
_interval: float = _DEFAULT_INTERVAL
_client: Any = None
_local_db_path: str | None = None
_local_enabled: bool = False  # opt-in: write presence to the local DB when no collector
_local_thread: threading.Thread | None = None  # local-mode daemon thread
_local_stop: threading.Event | None = None


def _agent_id() -> str:
    return os.environ.get("VOICEGW_AGENT_ID") or socket.gethostname()


def register_worker(
    agent_name: str,
    *,
    project: str = "default",
    tenant_id: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    region: str | None = None,
    version: str | None = None,
    interval: float = _DEFAULT_INTERVAL,
    local: bool = False,
    db_path: str | None = None,
    dispatch_name: str | None | _Unset = _UNSET,
) -> str:
    """Register this process as an agent worker and start heartbeating.

    Call once at worker boot. A collector (``collector_url`` /
    ``VOICEGW_COLLECTOR_URL``) takes precedence and pushes to its
    ``/v1/agents/heartbeat`` (needs a running event loop; call from your async
    entrypoint to appear while idle). With ``local=True`` and no collector it uses
    **local mode**: a background thread writes presence straight to the shared
    SQLite (``db_path`` / ``VOICEGW_DB_PATH`` / the default), which needs no event
    loop, so a worker registered in a plain ``__main__`` block is visible while idle
    right away. Without a collector and without ``local``, the worker is tracked in
    this process but nothing is pushed. ``api_key`` falls back to
    ``VOICEGW_API_KEY``; ``region`` to ``VOICEGW_REGION``. Returns the agent id.

    ``dispatch_name`` is the LiveKit agent_name this worker dispatches under, which
    the dashboard's probe uses to place a call by name. Left unset, it defaults to
    ``agent_name``: the name you register a LiveKit worker with is the name LiveKit
    dispatches to, so ``register_worker("reception")`` is probeable as "reception".
    Pass an explicit ``None`` for a worker with no LiveKit dispatch (a Pipecat
    agent), which keeps it in the roster but not probeable by name.
    """
    global _worker, _collector_url, _api_key, _interval, _local_db_path, _local_enabled
    from voicegateway._version import __version__

    resolved_dispatch = (
        agent_name if isinstance(dispatch_name, _Unset) else dispatch_name
    )
    _worker = _Worker(
        agent_id=_agent_id(),
        agent_name=agent_name,
        dispatch_name=resolved_dispatch,
        version=version or __version__,
        project=project,
        tenant_id=tenant_id,
        region=region or os.environ.get("VOICEGW_REGION"),
        host=socket.gethostname(),
        started_at=time.time(),
    )
    _collector_url = collector_url or os.environ.get("VOICEGW_COLLECTOR_URL")
    _api_key = api_key or os.environ.get("VOICEGW_API_KEY")
    _interval = interval
    _local_enabled = local
    _local_db_path = db_path or os.environ.get("VOICEGW_DB_PATH") or _DEFAULT_DB_PATH
    _ensure_running()
    if _collector_url and _pusher is None:
        # Collector mode registered outside a running event loop: the idle heartbeat
        # can't start yet, so this worker stays invisible until its first session
        # (attach -> bump_active) spins the pusher up. Local mode has no such caveat.
        logger.warning(
            "register_worker(%s): collector configured but no running event loop; "
            "the idle heartbeat starts on the first session. Call from your async "
            "entrypoint (or drop the collector to use the local-DB heartbeat).",
            _worker.agent_id,
        )
    return _worker.agent_id


def ensure_registered(
    agent_name: str,
    *,
    project: str = "default",
    tenant_id: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    region: str | None = None,
    version: str | None = None,
    interval: float = _DEFAULT_INTERVAL,
    local: bool = False,
    db_path: str | None = None,
    dispatch_name: str | None | _Unset = _UNSET,
) -> str:
    """Register + heartbeat only if this process has no worker yet.

    ``attach(heartbeat=True)`` calls this so it never clobbers an explicit
    ``register_worker`` done at ``__main__`` boot; if one exists, it just makes sure
    the heartbeat is running and returns its agent id.

    ``attach`` here passes ``agent_name`` = the VoiceGateway agent-id label (not a
    LiveKit name), so it also passes ``dispatch_name`` explicitly (the value it
    resolved from the job, or ``None`` off a LiveKit job) rather than letting it
    default to that label.
    """
    if _worker is not None:
        _ensure_running()
        return _worker.agent_id
    return register_worker(
        agent_name,
        project=project,
        tenant_id=tenant_id,
        collector_url=collector_url,
        api_key=api_key,
        region=region,
        version=version,
        interval=interval,
        local=local,
        db_path=db_path,
        dispatch_name=dispatch_name,
    )


def is_registered() -> bool:
    """Whether this process has a registered worker."""
    return _worker is not None


def bump_active(delta: int) -> None:
    """Move the active-session count (attach() calls +1 on open, -1 on close).

    A no-op when no worker is registered, so attach() stays decoupled from whether
    the agent opted into the fleet roster. Also (re)starts the heartbeat, so a worker
    that registered before its loop was running still begins heartbeating on its
    first session.
    """
    if _worker is None:
        return
    _worker.active_sessions = max(0, _worker.active_sessions + delta)
    _ensure_running()


def _ensure_running() -> None:
    """Start the heartbeat transport for the configured mode (idempotent).

    A collector takes precedence; else local mode runs only when opted in
    (``local=True``); else the worker is tracked but nothing is pushed.
    """
    if _worker is None:
        return
    if _collector_url:
        _ensure_pusher()
    elif _local_enabled:
        _ensure_local_thread()


# ---------------------------------------------------------------------------
# Collector mode (asyncio push to /v1/agents/heartbeat)
# ---------------------------------------------------------------------------


def _ensure_pusher() -> None:
    global _pusher
    if _pusher is not None or _worker is None or not _collector_url:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet; a later bump_active() (in an async context) starts it
    _pusher = loop.create_task(_run())


async def _run() -> None:
    try:
        while True:
            await push_once()
            await asyncio.sleep(_interval)
    except asyncio.CancelledError:
        pass


async def push_once() -> None:
    """Push one presence heartbeat to the collector. Best-effort."""
    global _client
    if _worker is None or not _collector_url:
        return
    try:
        import httpx

        if _client is None:
            _client = httpx.AsyncClient(timeout=10.0)
        headers = {"Authorization": f"Bearer {_api_key}"} if _api_key else {}
        await _client.post(
            _collector_url.rstrip("/") + "/v1/agents/heartbeat",
            json=_worker.presence(),
            headers=headers,
        )
    except Exception:  # noqa: BLE001 - presence is never load-bearing
        logger.debug("worker heartbeat push failed", exc_info=True)


# ---------------------------------------------------------------------------
# Local mode (daemon thread writing to the shared SQLite)
# ---------------------------------------------------------------------------


_atexit_registered = False


def _atexit_stop() -> None:
    """Signal + join the heartbeat thread on interpreter exit.

    Runs its ``finally`` (dispose the aiosqlite engine) rather than leaving the
    daemon thread's loop to be torn down abruptly, which avoids shutdown-time
    "Event loop is closed" noise on the agent's console. Best-effort.
    """
    if _local_stop is not None:
        _local_stop.set()
    if _local_thread is not None:
        _local_thread.join(timeout=2.0)


def _ensure_local_thread() -> None:
    global _local_thread, _local_stop, _atexit_registered
    if _local_thread is not None and _local_thread.is_alive():
        return
    if _worker is None or _local_db_path is None:
        return
    _local_stop = threading.Event()
    _local_thread = threading.Thread(
        target=_local_run,
        args=(_local_stop, _local_db_path, _interval),
        name="voicegw-fleet-heartbeat",
        daemon=True,
    )
    _local_thread.start()
    if not _atexit_registered:
        atexit.register(_atexit_stop)
        _atexit_registered = True


def _local_run(stop: threading.Event, db_path: str, interval: float) -> None:
    """Thread body: own event loop, write presence to the shared DB each interval.

    Brings the shared ``voicegw.db`` to head once on startup via ``run_migrations``
    (alembic upgrade), so a worker booting against a database older than its own
    code (a schema this release added a column to, say) writes cleanly instead of
    failing every interval until something else migrates it. That mattered for an
    idle agent with no dashboard up and no call yet: nothing else would have run
    a migration, and each heartbeat spewed a stack trace. This is ``alembic
    upgrade``, NOT ``create_all``: it stamps the version table, so it composes
    with the dashboard and the agent's own cost sink migrating the same file
    rather than seeding 18 un-stamped tables and poisoning a later upgrade.

    The write itself stays best-effort: if the one-time migration cannot run
    (a locked file, a genuinely broken DB), presence is never load-bearing, so a
    failed write is logged once and skipped rather than crashing the worker.
    """
    from voicegateway.core.config import GatewayConfig
    from voicegateway.core.database import Database
    from voicegateway.repository import workers_repository

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    conn = Database(
        GatewayConfig(cost_tracking={"db_path": os.path.expanduser(db_path)})
    )

    async def _write() -> None:
        w = _worker
        if w is None:
            return
        async with conn.session() as db:
            await workers_repository.upsert_heartbeat(db, w.presence())

    # Migrate the shared DB to head once, before the first write. Best-effort:
    # a failure here (a locked file, no write access) leaves the writes to fail
    # gracefully below, exactly as when this thread did not migrate at all.
    try:
        loop.run_until_complete(conn.run_migrations())
    except Exception:  # noqa: BLE001 - migration is best-effort; writes degrade gracefully
        logger.debug("local heartbeat: startup migration failed", exc_info=True)

    # A failed write is logged in full once, then quietly: an unmigratable or
    # locked DB must not stamp a stack trace into the agent's console every
    # interval for the life of the process.
    logged_write_failure = False
    try:
        while not stop.is_set():
            if _worker is not None:
                try:
                    loop.run_until_complete(_write())
                except Exception:  # noqa: BLE001 - presence is never load-bearing
                    if not logged_write_failure:
                        logger.debug("local heartbeat write failed", exc_info=True)
                        logged_write_failure = True
                    else:
                        logger.debug("local heartbeat write failed (suppressed trace)")
            stop.wait(interval)
    finally:
        try:
            loop.run_until_complete(conn.dispose())
        except Exception:  # noqa: BLE001
            pass
        loop.close()


async def aclose() -> None:
    """Stop every heartbeat transport (call on worker shutdown)."""
    global _pusher, _client, _local_thread, _local_stop
    if _pusher is not None:
        _pusher.cancel()
        try:
            await _pusher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _pusher = None
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None
    if _local_stop is not None:
        _local_stop.set()
    if _local_thread is not None:
        try:
            await asyncio.to_thread(_local_thread.join, 2.0)
        except Exception:  # noqa: BLE001
            pass
        _local_thread = None
        _local_stop = None
