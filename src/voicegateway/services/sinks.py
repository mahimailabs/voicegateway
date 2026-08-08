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
    from voicegateway.middleware.turn_tracker_middleware import TurnRow
    from voicegateway.models.request_model import RequestRecord
    from voicegateway.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Clamp Retry-After so an absurd server value cannot wedge the sink.
_RETRY_AFTER_MAX = 60.0


def _now() -> float:
    """Wall-clock seconds; isolated so the outbox timestamp is easy to reason about."""
    return datetime.now(tz=UTC).timestamp()


@runtime_checkable
class Sink(Protocol):
    """Narrow write target for captured telemetry.

    ``log_request`` persists one record. ``log_turns`` persists a batch of
    conversation turns. ``flush`` drains any buffered writes (a no-op for
    synchronous sinks). ``aclose`` releases resources.

    ``log_turns`` takes a batch rather than one row because TurnTracker already
    buffers to ``turn_buffer_flush_size`` before calling out, so a per-row
    method would only unpick batching the caller has already done.
    """

    async def log_request(self, record: RequestRecord) -> None: ...

    async def log_turns(self, rows: list[TurnRow]) -> None: ...

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

    async def log_turns(self, rows: list[TurnRow]) -> None:
        await self._storage.log_turns(rows)

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._storage.aclose()

    async def finalize_session_metrics(self, session_id: str) -> None:
        await self._storage.finalize_session_metrics(session_id)

    async def finalize_session_replay(self, session_id: str) -> None:
        await self._storage.finalize_session_replay(session_id)


def _normalize_ingest_url(url: str) -> str:
    """Resolve a collector base URL to its ``/v1/ingest`` endpoint, idempotently.

    ``VOICEGW_COLLECTOR_URL`` is the collector's base host (the heartbeat path is
    appended the same way). But older docs told users to include ``/v1/ingest``
    themselves, and appending it unconditionally produced ``/v1/ingest/v1/ingest``
    -> a silent 404. Accept both the base host and a full ingest URL so neither
    foot-guns the caller.
    """
    trimmed = url.rstrip("/")
    if trimmed.endswith("/v1/ingest"):
        return trimmed
    return trimmed + "/v1/ingest"


class RemoteCollectorSink:
    """Fleet sink: batches records and pushes them to a collector's /v1/ingest.

    Two durability modes, selected by ``spool_path``:

    **Best-effort (``spool_path=None``, default).** Cost telemetry is not
    billing-of-record: writes never block or fail the agent's hot path.
    Records buffer in memory and flush on batch size, on a periodic interval,
    or on ``aclose``. On a full in-memory buffer the oldest rows are dropped.
    A 429 is backpressure: the batch is retried with a clamped Retry-After
    delay and is never dropped on rate limiting. Other failed POSTs are
    retried with backoff up to ``max_retries`` and then dropped.

    **Durable store-and-forward (``spool_path`` set).** For invoice-grade
    fleet ingest where ``voicegw reconcile`` must trust that no cost rows were
    silently lost. Each ``log_request`` also appends the record to a tiny
    SQLite ``outbox`` table at ``spool_path``; rows are removed only once the
    collector acknowledges a batch (2xx). A batch that fails (or a 429 that
    cannot drain) stays in the outbox for a later flush or a later process, so
    a restart resumes delivery. The hot path stays non-blocking and
    non-raising: a spool write error is swallowed and counted, never
    propagated to the agent. ``completeness()`` reports ``sent``/``pending``/
    ``dropped`` so reconcile can tell whether fleet data is complete.
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
        spool_path: str | None = None,
        client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._ingest_url = _normalize_ingest_url(url)
        # Turns get their own route off the same base. See _post for why they
        # cannot share /v1/ingest.
        self._turns_url = self._ingest_url + "/turns"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer
        self._max_retries = max_retries
        self._backoff = backoff
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep or asyncio.sleep
        # In-memory batching buffer. Without a spool it holds bare payload
        # dicts (unchanged legacy shape). With a spool it holds
        # ``(seq, payload)`` so an ack can delete exactly those outbox rows.
        self._buffer: list[dict[str, Any]] = []
        self._spooled: list[tuple[int, dict[str, Any]]] = []
        # Turns batch in memory and are never written to the durable outbox.
        # The outbox exists so ``voicegw reconcile`` can prove no COST row was
        # lost; turns are timing metadata that reconcile does not read, and
        # spooling them would mean a schema change to outbox files already on
        # disk in deployed agents. Dropped turns are counted and logged.
        self._turn_buffer: list[dict[str, Any]] = []
        self._flusher: asyncio.Task[None] | None = None
        self._closed = False
        # Durable outbox (opt-in).
        self._spool_path = spool_path
        self._spool: Any | None = None  # aiosqlite.Connection when open
        self._spool_lock = asyncio.Lock()
        self._sent = 0
        self._dropped = 0
        # Hard cap on outbox rows: with a durable spool this should never be
        # hit under normal operation; it only guards against unbounded growth
        # when the collector is down indefinitely.
        self._spool_hard_cap = max(self._max_buffer * 100, 100_000)
        self._reloaded = False

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    # ------------------------------------------------------------------
    # Durable outbox (aiosqlite) — only touched when spool_path is set
    # ------------------------------------------------------------------

    async def _ensure_spool(self) -> Any:
        """Open the outbox connection and reload un-acked rows on first use.

        Idempotent: subsequent calls return the live connection. Creating the
        table and reloading the backlog happen exactly once, guarded by a lock
        so a burst of concurrent ``log_request`` calls cannot race the setup.
        """
        if self._spool is not None:
            return self._spool
        async with self._spool_lock:
            if self._spool is not None:  # double-checked after lock
                return self._spool
            import aiosqlite

            assert self._spool_path is not None
            conn = await aiosqlite.connect(self._spool_path)
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS outbox ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "payload TEXT NOT NULL, "
                "enqueued_at REAL NOT NULL)"
            )
            await conn.commit()
            self._spool = conn
            await self._reload_outbox(conn)
            return conn

    async def _reload_outbox(self, conn: Any) -> None:
        """Load un-acked rows (bounded by max_buffer) so a restart resumes."""
        if self._reloaded:
            return
        self._reloaded = True
        cursor = await conn.execute(
            "SELECT seq, payload FROM outbox ORDER BY seq LIMIT ?",
            (self._max_buffer,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        for seq, payload in rows:
            try:
                self._spooled.append((int(seq), json.loads(payload)))
            except (ValueError, TypeError):  # corrupt row: skip, do not crash
                logger.warning(
                    "RemoteCollectorSink outbox: skipped unreadable seq %s", seq
                )

    async def _spool_enqueue(self, payload: dict[str, Any]) -> int | None:
        """Durably append one payload; return its seq (or None on failure).

        Best-effort ethos: any storage error is swallowed and counted, never
        raised into the agent's hot path.
        """
        try:
            conn = await self._ensure_spool()
            cursor = await conn.execute(
                "INSERT INTO outbox (payload, enqueued_at) VALUES (?, ?)",
                (json.dumps(payload), _now()),
            )
            seq = cursor.lastrowid
            await cursor.close()
            await conn.commit()
            await self._enforce_spool_cap(conn)
            return int(seq) if seq is not None else None
        except Exception as exc:  # noqa: BLE001 - hot path must never raise
            self._dropped += 1
            logger.warning("RemoteCollectorSink outbox write failed: %s", exc)
            return None

    async def _enforce_spool_cap(self, conn: Any) -> None:
        """Trim the oldest outbox rows if the hard cap is exceeded."""
        cursor = await conn.execute("SELECT COUNT(*) FROM outbox")
        row = await cursor.fetchone()
        await cursor.close()
        count = int(row[0]) if row else 0
        if count <= self._spool_hard_cap:
            return
        overflow = count - self._spool_hard_cap
        await conn.execute(
            "DELETE FROM outbox WHERE seq IN "
            "(SELECT seq FROM outbox ORDER BY seq LIMIT ?)",
            (overflow,),
        )
        await conn.commit()
        self._dropped += overflow
        logger.warning(
            "RemoteCollectorSink outbox exceeded hard cap; dropped %d oldest row(s)",
            overflow,
        )

    async def _spool_ack(self, seqs: list[int]) -> None:
        """Delete acked rows from the outbox after the collector confirms 2xx."""
        if not seqs or self._spool is None:
            return
        try:
            placeholders = ",".join("?" for _ in seqs)
            await self._spool.execute(
                f"DELETE FROM outbox WHERE seq IN ({placeholders})", seqs
            )
            await self._spool.commit()
        except Exception as exc:  # noqa: BLE001 - never propagate
            logger.warning("RemoteCollectorSink outbox ack failed: %s", exc)

    async def _spool_pending(self) -> int:
        """Current outbox row count (0 when no spool is configured)."""
        if self._spool_path is None:
            return 0
        try:
            conn = await self._ensure_spool()
            cursor = await conn.execute("SELECT COUNT(*) FROM outbox")
            row = await cursor.fetchone()
            await cursor.close()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001
            return 0

    async def completeness(self) -> dict[str, int]:
        """Report delivery completeness for reconcile-against-fleet.

        ``sent`` is acked rows, ``pending`` is the live outbox row count, and
        ``dropped`` is rows lost only when the outbox itself overflowed its
        hard cap (0 under normal operation with a durable spool). Without a
        spool, ``pending`` is always 0 and ``dropped`` counts the in-memory
        drop-oldest and post-retry drops.
        """
        return {
            "sent": self._sent,
            "pending": await self._spool_pending(),
            "dropped": self._dropped,
        }

    async def aclose_spool(self) -> None:
        """Close the outbox connection without flushing (used for restart tests)."""
        if self._spool is not None:
            try:
                await self._spool.close()
            except Exception:  # noqa: BLE001
                pass
            self._spool = None

    async def log_request(self, record: RequestRecord) -> None:
        payload = asdict(record)
        if self._spool_path is not None:
            # Durable path: persist first, then batch the (seq, payload) pair so
            # an ack can delete exactly this row. A failed spool write returns
            # None (counted, not raised) and the row is dropped rather than
            # buffered un-tracked.
            seq = await self._spool_enqueue(payload)
            if seq is not None:
                self._spooled.append((seq, payload))
            if len(self._spooled) > self._max_buffer:
                overflow = len(self._spooled) - self._max_buffer
                del self._spooled[:overflow]
            self._maybe_start_flusher()
            if len(self._spooled) >= self._batch_size:
                await self.flush()
            return
        self._buffer.append(payload)
        if len(self._buffer) > self._max_buffer:
            overflow = len(self._buffer) - self._max_buffer
            del self._buffer[:overflow]
            self._dropped += overflow
            logger.warning(
                "RemoteCollectorSink buffer full; dropped %d oldest row(s)", overflow
            )
        self._maybe_start_flusher()
        if len(self._buffer) >= self._batch_size:
            await self.flush()

    async def log_turns(self, rows: list[TurnRow]) -> None:
        """Buffer a batch of turns for the collector's ``/v1/ingest/turns``.

        Best-effort by the same rule as ``log_request``: this runs on the
        agent's hot path at the end of a turn, so it never blocks and never
        raises. Overflow drops oldest, counted and logged rather than silent.
        """
        if not rows:
            return
        self._turn_buffer.extend(asdict(row) for row in rows)
        if len(self._turn_buffer) > self._max_buffer:
            overflow = len(self._turn_buffer) - self._max_buffer
            del self._turn_buffer[:overflow]
            self._dropped += overflow
            logger.warning(
                "RemoteCollectorSink turn buffer full; dropped %d oldest turn(s)",
                overflow,
            )
        self._maybe_start_flusher()
        if len(self._turn_buffer) >= self._batch_size:
            await self._flush_turns()

    async def _flush_turns(self) -> None:
        """Drain the turn buffer to the collector. Requeues on failure."""
        if not self._turn_buffer:
            return
        batch = self._turn_buffer
        self._turn_buffer = []
        if not await self._post(batch, self._turns_url):
            # Put them back so the next flush (or aclose) retries. _post has
            # already exhausted its own retries and logged the reason; the
            # buffer cap above is what bounds this.
            self._turn_buffer = batch + self._turn_buffer

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
        # Turns drain on every flush, including the spool path. They share the
        # periodic flusher and aclose with requests, but not the outbox, so this
        # runs regardless of which request path is taken below.
        await self._flush_turns()
        if self._spool_path is not None:
            if not self._spooled:
                return
            tracked = self._spooled
            self._spooled = []
            seqs = [seq for seq, _ in tracked]
            payloads = [payload for _, payload in tracked]
            if await self._post(payloads):
                # Acked: drop from the outbox and count. On failure, requeue so
                # a later flush (or a later process via the outbox) retries.
                await self._spool_ack(seqs)
                self._sent += len(payloads)
            else:
                self._spooled = tracked + self._spooled
            return
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        await self._post(batch)

    async def _post(self, batch: list[dict[str, Any]], url: str | None = None) -> bool:
        """POST a batch; return True on ack (2xx), False if it was not delivered.

        The return value drives the durable-spool ack/requeue decision. The
        legacy (no-spool) caller ignores it, preserving best-effort behavior.

        ``url`` defaults to the ingest endpoint. Turns pass their own, because
        the ingest handler builds a RequestRecord out of every dict it is given
        and counts anything that fails to build as malformed. A turn row sent
        there would be answered 200 and dropped.
        """
        target = url if url is not None else self._ingest_url
        client = self._ensure_client()
        attempt = 0
        while True:
            reason: Any
            try:
                resp = await client.post(target, json=batch, headers=self._headers)
                if resp.status_code < 400:
                    return True
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
                if self._spool_path is None:
                    self._dropped += len(batch)
                logger.warning(
                    "RemoteCollectorSink dropped %d row(s) after %d attempt(s): %s",
                    len(batch),
                    attempt + 1,
                    reason,
                )
                return False
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
        await self.aclose_spool()
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
