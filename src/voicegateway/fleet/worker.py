"""Process-level worker presence for the fleet roster.

The LiveKit server API lists agents that are IN a room, but not the idle/registered
worker pool, so ``voicegw livekit agents`` can only ever show busy agents. This
closes that gap from the agent side: a worker calls :func:`register_worker` once at
boot, and the process then pushes a periodic heartbeat to the collector's
``/v1/agents/heartbeat``. The heartbeat carries an idle/busy status that
``voicegateway.attach`` keeps accurate by bumping an active-session counter on each
session open/close, plus the version, project, host, and region the LiveKit server
never knows.

Best-effort by design: it never blocks or breaks the agent. No collector configured
means the registry still tracks status locally but nothing is pushed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Milliseconds is overkill for presence; a quarter-minute keeps the roster fresh
# without hammering the collector. The read side ages a worker out after a small
# multiple of this (see the cloud /v1/agents TTL).
_DEFAULT_INTERVAL = 15.0


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
_pusher: asyncio.Task | None = None
_collector_url: str | None = None
_api_key: str | None = None
_interval: float = _DEFAULT_INTERVAL
_client: Any = None


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
) -> str:
    """Register this process as an agent worker and start heartbeating.

    Call once at worker boot (ideally inside the running event loop, so the periodic
    push starts immediately and an idle worker is visible before its first call).
    ``collector_url`` / ``api_key`` fall back to ``VOICEGW_COLLECTOR_URL`` /
    ``VOICEGW_API_KEY``; ``region`` to ``VOICEGW_REGION``. Returns the agent id.
    """
    global _worker, _collector_url, _api_key, _interval
    from voicegateway._version import __version__

    _worker = _Worker(
        agent_id=_agent_id(),
        agent_name=agent_name,
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
    _ensure_pusher()
    if _collector_url and _pusher is None:
        # Registered outside a running event loop: the idle heartbeat can't start
        # yet, so this worker stays invisible in the roster until its first
        # session (attach -> bump_active) spins the pusher up. Call from your
        # async entrypoint to be visible while idle.
        logger.warning(
            "register_worker(%s): no running event loop; the idle heartbeat will "
            "not start until the first session. Call register_worker from your "
            "async agent entrypoint to appear in the roster while idle.",
            _worker.agent_id,
        )
    return _worker.agent_id


def bump_active(delta: int) -> None:
    """Move the active-session count (attach() calls +1 on open, -1 on close).

    A no-op when no worker is registered, so attach() stays decoupled from whether
    the agent opted into the fleet roster. Also (re)starts the pusher, so a worker
    that registered before its loop was running still begins heartbeating on its
    first session.
    """
    if _worker is None:
        return
    _worker.active_sessions = max(0, _worker.active_sessions + delta)
    _ensure_pusher()


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
    """Push one presence heartbeat. Best-effort: a failure is swallowed."""
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


async def aclose() -> None:
    """Stop the pusher and close the client (call on worker shutdown)."""
    global _pusher, _client
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
