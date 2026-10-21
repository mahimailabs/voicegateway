"""Bounded queue and one background flush task that POSTs v1 batches.

Fails open: a full queue drops and counts, a refused or unreachable endpoint
drops and counts, and nothing raises into the agent. No LiveKit import here.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ._version import __version__

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger("voicegateway")

SDK_NAME = "voicegateway-python"
FRAMEWORK_NAME = "livekit-agents"

MAX_QUEUE = 10_000
BATCH_SIZE = 100
FLUSH_INTERVAL = 1.0
DRAIN_TIMEOUT = 2.0
ATTEMPTS = 3
BACKOFF = 0.2
REQUEST_TIMEOUT = 5.0
# The contract's limit on one batch, decompressed (docs/specs/python-sdk.md).
MAX_BATCH_BYTES = 1024 * 1024


def rfc3339(ts: float | None = None) -> str:
    """A Unix time (now when None) as RFC 3339 UTC with milliseconds."""
    dt = datetime.now(UTC) if ts is None else datetime.fromtimestamp(ts, UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Delivery:
    def __init__(
        self,
        *,
        url: str,
        token: Callable[[], str],
        call: dict[str, str | None],
        framework_version: str,
        max_queue: int = MAX_QUEUE,
        batch_size: int = BATCH_SIZE,
        interval: float = FLUSH_INTERVAL,
        drain_timeout: float = DRAIN_TIMEOUT,
        attempts: int = ATTEMPTS,
        backoff: float = BACKOFF,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._endpoint = url.rstrip("/") + "/sdk/v1/events"
        self._token = token
        self._header = {
            "version": 1,
            "sdk": {"name": SDK_NAME, "version": __version__},
            "framework": {"name": FRAMEWORK_NAME, "version": framework_version},
            "call": call,
        }
        self._max_queue = max_queue
        self._batch_size = batch_size
        self._interval = interval
        self._drain_timeout = drain_timeout
        self._attempts = attempts
        self._backoff = backoff
        self._request_timeout = request_timeout

        self._queue: deque[dict[str, Any]] = deque()
        self._inflight: list[dict[str, Any]] = []
        self._wake = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task[None] | None = None
        self._warned = False
        self.dropped = 0

    def put(self, type_: str, at: str, data: dict[str, Any]) -> None:
        """Enqueue one event. Never blocks, never raises; a full queue drops and counts."""
        if self._closing:
            return
        if len(self._queue) >= self._max_queue:
            self._drop(1, "the event queue is full")
            return
        self._queue.append({"type": type_, "at": at, "data": data})
        if len(self._queue) >= self._batch_size:
            self._wake.set()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="voicegateway-flush")

    async def aclose(self) -> None:
        """Flush what is queued, then stop; gives up after drain_timeout and counts the rest."""
        if self._closing:
            return
        self._closing = True
        self._wake.set()
        task = self._task
        if task is None:
            self._drop(len(self._queue), "the flush task never started")
            self._queue.clear()
            return
        _, pending = await asyncio.wait({task}, timeout=self._drain_timeout)
        if pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._drop(
                len(self._queue) + len(self._inflight),
                f"not delivered within {self._drain_timeout:g} s of close",
            )
            self._queue.clear()
            self._inflight = []

    async def _run(self) -> None:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self._request_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            while True:
                if not self._closing:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), self._interval)
                self._wake.clear()
                try:
                    await self._flush(http)
                except Exception:
                    # Never let the flush task die; whatever was in flight is counted.
                    logger.debug("voicegateway: flush failed", exc_info=True)
                    self._drop(len(self._inflight), "the flush failed")
                    self._inflight = []
                if self._closing and not self._queue:
                    return

    async def _flush(self, http: aiohttp.ClientSession) -> None:
        while self._queue:
            n = min(self._batch_size, len(self._queue))
            self._inflight = [self._queue.popleft() for _ in range(n)]
            await self._send(http, self._inflight)
            self._inflight = []
            if not self._closing and len(self._queue) < self._batch_size:
                return

    async def _send(self, http: aiohttp.ClientSession, events: list[dict[str, Any]]) -> None:
        raw = json.dumps(
            {**self._header, "batchId": uuid.uuid4().hex, "events": events},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(raw) > MAX_BATCH_BYTES:
            if len(events) == 1:
                self._drop(1, "an event is over the 1 MiB batch limit")
                return
            half = len(events) // 2
            await self._send(http, events[:half])
            await self._send(http, events[half:])
            return
        await self._post(http, gzip.compress(raw), len(events))

    async def _post(self, http: aiohttp.ClientSession, body: bytes, count: int) -> None:
        import aiohttp

        reason = "the endpoint did not answer"
        for attempt in range(self._attempts):
            if attempt:
                await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
            headers = {
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "User-Agent": f"{SDK_NAME}/{__version__}",
            }
            try:
                async with http.post(self._endpoint, data=body, headers=headers) as resp:
                    status = resp.status
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                reason = f"the endpoint did not answer ({type(exc).__name__})"
                continue
            if 200 <= status < 300:
                return
            reason = f"the endpoint answered HTTP {status}"
            if status != 429 and status < 500:
                break
        self._drop(count, reason)

    def _drop(self, count: int, reason: str) -> None:
        if count <= 0:
            return
        self.dropped += count
        if not self._warned:
            self._warned = True
            logger.warning(
                "voicegateway: dropped %d event(s): %s; the agent carries on, "
                "and later drops are counted on Attachment.dropped",
                count,
                reason,
            )
