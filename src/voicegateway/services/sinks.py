"""Write sinks: the seam between record producers and where rows land.

A ``Sink`` is the narrow write interface every telemetry producer (the
inference middleware and the ``attach()`` capture pipeline) targets. In
single-node mode the sink is :class:`LocalSqliteSink`, which writes to the
embedded SQLite ``StorageService``. In fleet mode it is the
``RemoteCollectorSink`` (added in a later build step), which batches rows
and pushes them to a collector. Producers stay identical; only the sink
swaps.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)


@runtime_checkable
class Sink(Protocol):
    """Narrow write target for request records.

    ``log_request`` persists one record. ``flush`` drains any buffered
    writes (a no-op for synchronous sinks). ``aclose`` releases resources.
    """

    async def log_request(self, record: RequestRecord) -> None: ...

    async def flush(self) -> None: ...

    async def aclose(self) -> None: ...


class LocalSqliteSink:
    """Default single-node sink: writes through the embedded StorageService.

    Delegates ``log_request`` and the session-finalization hooks to the
    wrapped storage so ``CostTracker.close_session`` (which resolves
    ``finalize_session_*`` by ``getattr``) keeps working unchanged. ``flush``
    is a no-op because each SQLite write commits on its own.
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    async def log_request(self, record: RequestRecord) -> None:
        await self._storage.log_request(record)

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._storage.aclose()

    async def finalize_session_metrics(self, session_id: str) -> None:
        await self._storage.finalize_session_metrics(session_id)

    async def finalize_session_replay(self, session_id: str) -> None:
        await self._storage.finalize_session_replay(session_id)


class RemoteCollectorSink:
    """Fleet sink: batches records and pushes them to a collector's /v1/ingest.

    Best-effort by design (cost telemetry is not billing-of-record): writes
    never block or fail the agent's hot path. Records buffer in memory and
    flush on batch size, on a periodic interval, or on ``aclose``. On a full
    buffer the oldest rows are dropped; failed POSTs are retried with backoff
    up to ``max_retries`` and then dropped.
    """

    def __init__(
        self,
        url: str,
        virtual_key: str | None,
        *,
        batch_size: int = 20,
        flush_interval: float | None = 2.0,
        max_buffer: int = 1000,
        max_retries: int = 2,
        backoff: float = 0.2,
        client: Any | None = None,
    ) -> None:
        self._ingest_url = url.rstrip("/") + "/v1/ingest"
        self._headers = (
            {"Authorization": f"Bearer {virtual_key}"} if virtual_key else {}
        )
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._max_retries = max_retries
        self._backoff = backoff
        self._client = client
        self._owns_client = client is None
        self._buffer: list[dict[str, Any]] = []
        self._flusher: asyncio.Task[None] | None = None
        self._closed = False

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def log_request(self, record: RequestRecord) -> None:
        self._buffer.append(asdict(record))
        if len(self._buffer) > self._max_buffer:
            overflow = len(self._buffer) - self._max_buffer
            del self._buffer[:overflow]
            logger.warning(
                "RemoteCollectorSink buffer full; dropped %d oldest row(s)", overflow
            )
        self._maybe_start_flusher()
        if len(self._buffer) >= self._batch_size:
            await self.flush()

    def _maybe_start_flusher(self) -> None:
        if not self._flush_interval or self._flusher is not None or self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flusher = loop.create_task(self._periodic_flush())

    async def _periodic_flush(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._flush_interval or 0)
                await self.flush()
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        await self._post(batch)

    async def _post(self, batch: list[dict[str, Any]]) -> None:
        client = self._ensure_client()
        attempt = 0
        while True:
            reason: Any
            try:
                resp = await client.post(
                    self._ingest_url, json=batch, headers=self._headers
                )
                if resp.status_code < 400:
                    return
                reason = f"HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001 - best-effort, never propagate
                reason = exc
            if attempt >= self._max_retries:
                logger.warning(
                    "RemoteCollectorSink dropped %d row(s) after %d attempt(s): %s",
                    len(batch),
                    attempt + 1,
                    reason,
                )
                return
            await asyncio.sleep(self._backoff * (2**attempt))
            attempt += 1

    async def aclose(self) -> None:
        self._closed = True
        if self._flusher is not None:
            self._flusher.cancel()
            try:
                await self._flusher
            except asyncio.CancelledError:
                pass
            self._flusher = None
        await self.flush()
        if self._owns_client and self._client is not None:
            await self._client.aclose()


__all__ = ["LocalSqliteSink", "RemoteCollectorSink", "Sink"]
