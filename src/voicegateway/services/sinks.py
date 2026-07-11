"""Write sinks: the seam between record producers and where rows land.

A ``Sink`` is the narrow write interface every telemetry producer (the
inference middleware and the ``attach()`` capture pipeline) targets. In
single-node mode the sink is :class:`LocalSqliteSink`, which writes to the
embedded SQLite ``StorageService``. In fleet mode it is the
``RemoteCollectorSink`` (added in a later build step), which batches rows
and pushes them to a collector. When a ClickHouse host is configured, the
:class:`ClickHouseSink` routes telemetry writes directly to ClickHouse via
async_insert batching with an idempotent deduplication token. Producers stay
identical; only the sink swaps.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Clamp Retry-After so an absurd server value cannot wedge the sink.
_RETRY_AFTER_MAX = 60.0


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
    in-memory buffer the oldest rows are dropped. A 429 is backpressure: the
    batch is retried with a clamped Retry-After delay and is never dropped on
    rate limiting. Other failed POSTs are retried with backoff up to
    ``max_retries`` and then dropped.
    """

    def __init__(
        self,
        url: str,
        api_key: str | None,
        *,
        batch_size: int = 20,
        flush_interval: float | None = 2.0,
        max_buffer: int = 1000,
        max_retries: int = 2,
        backoff: float = 0.2,
        client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._ingest_url = url.rstrip("/") + "/v1/ingest"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._max_retries = max_retries
        self._backoff = backoff
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep or asyncio.sleep
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
                if resp.status_code == 429:
                    # Backpressure, not failure: honor Retry-After (clamped) and
                    # retry the same batch WITHOUT counting toward max_retries,
                    # so a 429 never drops the in-flight batch. Memory is bounded
                    # by the log_request buffer drop-oldest, not by dropping here.
                    await self._sleep(self._retry_after_seconds(resp))
                    continue
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
            await self._sleep(self._backoff * (2**attempt))
            attempt += 1

    def _retry_after_seconds(self, resp: Any) -> float:
        """Parse a Retry-After header into seconds, clamped to a sane maximum.

        Falls back to the configured backoff when the header is absent or not a
        non-negative integer (we only ever emit integer-second Retry-After).
        """
        try:
            raw = resp.headers.get("retry-after")
        except Exception:  # noqa: BLE001 - a malformed response must not propagate
            raw = None
        if raw is None:
            return self._backoff
        try:
            seconds = float(int(str(raw).strip()))
        except (TypeError, ValueError):
            return self._backoff
        if seconds < 0:
            return self._backoff
        return min(seconds, _RETRY_AFTER_MAX)

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


# ---------------------------------------------------------------------------
# ClickHouse sink
# ---------------------------------------------------------------------------

_CH_COLUMN_NAMES: list[str] = [
    "tenant_id",
    "id",
    "timestamp",
    "project",
    "modality",
    "provider",
    "model_id",
    "input_units",
    "output_units",
    "cached_input_units",
    "cost_usd",
    "pricing_source",
    "rated_price_usd",
    "rate_rule",
    "ttfb_ms",
    "total_latency_ms",
    "status",
    "fallback_from",
    "error_message",
    "session_id",
    "agent_id",
    "metadata",
]

_CH_INSERT_SETTINGS: dict[str, Any] = {
    "async_insert": 1,
    "wait_for_async_insert": 1,
    "async_insert_deduplicate": 1,
}


class ClickHouseSink:
    """ClickHouse sink: batches rows and inserts via clickhouse-connect async client.

    ``log_request`` captures ``current_tenant()`` **at enqueue time** (not at
    flush time) so background-task flushes do not mis-attribute rows. ``flush``
    does a columnar insert with ``async_insert=1, wait_for_async_insert=1,
    async_insert_deduplicate=1`` and an ``insert_deduplication_token`` that is
    the SHA-256 of the batch's sorted record ids. Re-sending the same batch with
    the same token is a ClickHouse no-op (idempotent retry). The internal buffer
    is rebuilt fresh before each insert; clickhouse-connect consumes/mutates its
    input.

    Create via ``await ClickHouseSink.create(...)``; share one async client
    per process (pass ``client=`` to reuse an existing one, or let ``create``
    build a new one and store it on ``app.state.ch_client``).
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._buffer: list[dict[str, Any]] = []

    @classmethod
    async def create(
        cls,
        *,
        host: str,
        port: int = 8123,
        username: str = "default",
        password: str = "",
        database: str = "telemetry",
        client: Any | None = None,
    ) -> ClickHouseSink:
        """Async factory: create (or accept) an async clickhouse-connect client."""
        if client is None:
            import clickhouse_connect

            client = await clickhouse_connect.get_async_client(
                host=host,
                port=port,
                username=username,
                password=password,
                database=database,
            )
        return cls(client)

    # ------------------------------------------------------------------
    # Row-building helpers (instance methods; tests call via instance)
    # ------------------------------------------------------------------

    def _build_row(
        self,
        record: RequestRecord,
        tenant_id: str | None,
    ) -> dict[str, Any]:
        """Convert a RequestRecord + server-stamped tenant into a CH row dict.

        ``timestamp`` is emitted as a timezone-aware ``datetime`` so
        clickhouse-connect maps it to ``DateTime64(3,'UTC')`` correctly.
        The ``_version`` column is MATERIALIZED in ClickHouse; do NOT include it.
        """
        ts: datetime = datetime.fromtimestamp(record.timestamp, tz=UTC)

        return {
            "tenant_id": tenant_id or "",
            "id": record.id,
            "timestamp": ts,
            "project": record.project or "default",
            "modality": record.modality,
            "provider": record.provider,
            "model_id": record.model_id,
            "input_units": record.input_units,
            "output_units": record.output_units,
            "cached_input_units": record.cached_input_units,
            "cost_usd": record.cost_usd,
            "pricing_source": record.pricing_source or "",
            "rated_price_usd": record.rated_price_usd,
            "rate_rule": record.rate_rule or "",
            "ttfb_ms": record.ttfb_ms,
            "total_latency_ms": record.total_latency_ms,
            "status": record.status or "success",
            "fallback_from": record.fallback_from or "",
            "error_message": record.error_message or "",
            "session_id": record.session_id or "",
            "agent_id": record.agent_id or "",
            "metadata": json.dumps(record.metadata) if record.metadata else "{}",
        }

    @staticmethod
    def _dedup_token(ids: list[str]) -> str:
        """SHA-256 hex digest of sorted ids joined by newlines.

        Deterministic across invocations: sorting makes the token independent
        of buffer insertion order. Identical re-POST with the same token is a
        ClickHouse no-op (``async_insert_deduplicate=1``).
        """
        payload = "\n".join(sorted(ids))
        return hashlib.sha256(payload.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Sink Protocol implementation
    # ------------------------------------------------------------------

    async def log_request(self, record: RequestRecord) -> None:
        """Buffer one record, capturing the tenant ContextVar RIGHT NOW.

        CRITICAL: read ``current_tenant()`` here, at enqueue time. ``flush``
        may run in a background asyncio Task whose context does not carry the
        originating request's tenant; reading there would yield None and
        mis-attribute every row in the batch.
        """
        from voicegateway.inference.session.context import current_tenant

        tenant_id = current_tenant()
        row = self._build_row(record, tenant_id)
        self._buffer.append(row)

    async def flush(self) -> None:
        """Insert all buffered rows into ``telemetry.requests`` and reset buffer.

        Builds a fresh list copy before inserting (clickhouse-connect consumes
        / mutates the data structure). Computes an idempotent dedup token so
        a retry of the same batch is a server-side no-op.
        """
        if not self._buffer:
            return

        rows = list(self._buffer)
        self._buffer = []

        ids = [row["id"] for row in rows]
        token = self._dedup_token(ids)

        data = [[row[col] for col in _CH_COLUMN_NAMES] for row in rows]
        settings = {**_CH_INSERT_SETTINGS, "insert_deduplication_token": token}

        await self._client.insert(
            "telemetry.requests",
            data,
            column_names=_CH_COLUMN_NAMES,
            settings=settings,
        )
        logger.debug(
            "ClickHouseSink: flushed %d row(s) with dedup token %s",
            len(rows),
            token[:8],
        )

    async def aclose(self) -> None:
        """Flush remaining rows and release the async client."""
        await self.flush()
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["ClickHouseSink", "LocalSqliteSink", "RemoteCollectorSink", "Sink"]
