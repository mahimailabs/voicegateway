"""Bounded, restart-safe store-and-forward for accounting envelopes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

from voicegateway.accounting.contracts import UsageBatchResponse, UsageEnvelope

logger = logging.getLogger(__name__)


class AccountingOutbox:
    """Persist before delivery and delete only after a durable receipt."""

    def __init__(
        self,
        path: str | Path,
        collector_url: str,
        *,
        api_key: str | None = None,
        max_records: int = 100_000,
        max_bytes: int = 64 * 1024 * 1024,
        memory_queue_size: int = 1_000,
        max_delivery_attempts: int = 12,
        client: Any | None = None,
    ) -> None:
        self._path = Path(path).expanduser()
        self._url = collector_url.rstrip("/") + "/v1/accounting/usage"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._max_delivery_attempts = max_delivery_attempts
        self._queue: asyncio.Queue[UsageEnvelope] = asyncio.Queue(memory_queue_size)
        self._client = client
        self._owns_client = client is None
        self._db: aiosqlite.Connection | None = None
        self._worker: asyncio.Task[None] | None = None
        self._capture_failures = 0
        self._submit_lock = asyncio.Lock()
        self._failure_lock = asyncio.Lock()

    async def _open(self) -> aiosqlite.Connection:
        if self._db is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self._path)
            await self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending (
                    event_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    enqueued_at_ns INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS rejected (
                    event_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    code TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO health_counters(name, value)
                VALUES ('capture_failures', 0);
                """
            )
            columns = {
                row[1]
                for row in list(
                    await self._db.execute_fetchall("PRAGMA table_info(pending)")
                )
            }
            if "enqueued_at_ns" not in columns:
                await self._db.execute(
                    "ALTER TABLE pending ADD COLUMN enqueued_at_ns INTEGER NOT NULL DEFAULT 0"
                )
            await self._db.commit()
            await self._persist_failures(self._db)
        return self._db

    async def submit(self, envelope: UsageEnvelope) -> str:
        """Durably enqueue and return ``stored``, ``duplicate``, or ``full``."""
        db = await self._open()
        payload = envelope.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        async with self._submit_lock:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = list(
                    await db.execute_fetchall(
                        "SELECT payload_hash FROM pending WHERE event_id = ?",
                        (envelope.event_id,),
                    )
                )
                if existing:
                    await db.rollback()
                    if existing[0][0] != digest:
                        raise ValueError(
                            "event identity already queued with different content"
                        )
                    return "duplicate"
                count, used_bytes = list(
                    await db.execute_fetchall(
                        "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0) FROM pending"
                    )
                )[0]
                if (
                    count >= self._max_records
                    or used_bytes + len(payload.encode()) > self._max_bytes
                ):
                    await db.rollback()
                    await self._increment_failure()
                    return "full"
                await db.execute(
                    "INSERT INTO pending(event_id, payload_hash, payload, enqueued_at_ns) VALUES (?, ?, ?, ?)",
                    (envelope.event_id, digest, payload, time.time_ns()),
                )
                await db.commit()
                return "stored"
            except BaseException:
                await db.rollback()
                raise

    def enqueue_nowait(self, envelope: UsageEnvelope) -> bool:
        """Non-blocking voice-path capture; False is an explicit loss signal."""
        self.start()
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            asyncio.create_task(self._increment_failure())
            logger.error("accounting capture queue full")
            return False
        return True

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._persist_worker())

    async def _persist_worker(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                outcome = await self.submit(envelope)
                if outcome == "full":
                    logger.error("accounting outbox full; event was not persisted")
            except Exception:
                await self._increment_failure()
                logger.exception("accounting outbox persistence failed")
            finally:
                self._queue.task_done()

    async def drain(self, *, limit: int = 100) -> dict[str, int]:
        db = await self._open()
        rows = list(
            await db.execute_fetchall(
                "SELECT event_id, payload FROM pending ORDER BY rowid LIMIT ?", (limit,)
            )
        )
        if not rows:
            return {"accepted": 0, "duplicate": 0, "rejected": 0, "retryable": 0}
        client = self._client
        if client is None:
            import httpx

            client = self._client = httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(
                self._url,
                headers=self._headers,
                json=[json.loads(payload) for _, payload in rows],
            )
            if response.status_code >= 500 or response.status_code == 429:
                quarantined = await self._mark_retryable(
                    db, rows, f"http_{response.status_code}"
                )
                return {
                    "accepted": 0,
                    "duplicate": 0,
                    "rejected": quarantined,
                    "retryable": len(rows) - quarantined,
                }
            if 400 <= response.status_code < 500:
                await self._quarantine(db, rows, f"http_{response.status_code}")
                return {
                    "accepted": 0,
                    "duplicate": 0,
                    "rejected": len(rows),
                    "retryable": 0,
                }
            response.raise_for_status()
            receipts = UsageBatchResponse.model_validate(response.json()).receipts
        except Exception:
            quarantined = await self._mark_retryable(db, rows, "delivery_error")
            return {
                "accepted": 0,
                "duplicate": 0,
                "rejected": quarantined,
                "retryable": len(rows) - quarantined,
            }
        by_id = {receipt.event_id: receipt for receipt in receipts}
        counts = {"accepted": 0, "duplicate": 0, "rejected": 0, "retryable": 0}
        for event_id, payload in rows:
            receipt = by_id.get(event_id)
            if receipt is None or receipt.outcome == "retryable":
                await db.execute(
                    "UPDATE pending SET attempts = attempts + 1, last_error_code = ? WHERE event_id = ?",
                    ("missing_receipt" if receipt is None else receipt.code, event_id),
                )
                counts["retryable"] += 1
            elif receipt.outcome in {"accepted", "duplicate"} and bool(
                receipt.receipt_id
            ):
                await db.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                counts[receipt.outcome] += 1
            elif receipt.outcome == "rejected":
                digest = hashlib.sha256(payload.encode()).hexdigest()
                await db.execute(
                    "INSERT OR REPLACE INTO rejected VALUES (?, ?, ?, ?)",
                    (event_id, digest, payload, receipt.code),
                )
                await db.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                counts["rejected"] += 1
            else:
                await db.execute(
                    "UPDATE pending SET attempts = attempts + 1, last_error_code = ? WHERE event_id = ?",
                    ("invalid_receipt", event_id),
                )
                counts["retryable"] += 1
        await db.commit()
        return counts

    async def drain_with_backoff(
        self,
        *,
        limit: int = 100,
        max_attempts: int = 3,
        base_delay: float = 0.2,
        max_delay: float = 5.0,
        sleep: Any = asyncio.sleep,
        jitter: Any = random.uniform,
    ) -> dict[str, int]:
        """Retry a drain with bounded exponential backoff."""
        result = {"accepted": 0, "duplicate": 0, "rejected": 0, "retryable": 0}
        for attempt in range(max(1, max_attempts)):
            current = await self.drain(limit=limit)
            for key in result:
                result[key] += current[key] if key != "retryable" else 0
            result["retryable"] = current["retryable"]
            if current["retryable"] == 0:
                break
            if attempt + 1 < max_attempts:
                ceiling = min(max_delay, base_delay * (2**attempt))
                await sleep(jitter(ceiling / 2, ceiling))
        return result

    async def _mark_retryable(
        self, db: aiosqlite.Connection, rows: Iterable[Any], code: str
    ) -> int:
        attempts: dict[str, int] = {}
        for event_id, value in await db.execute_fetchall(
            "SELECT event_id, attempts FROM pending"
        ):
            attempts[str(event_id)] = int(value)
        quarantine = [
            row
            for row in rows
            if attempts.get(row[0], 0) + 1 >= self._max_delivery_attempts
        ]
        retry = [row for row in rows if row not in quarantine]
        await db.executemany(
            "UPDATE pending SET attempts = attempts + 1, last_error_code = ? WHERE event_id = ?",
            [(code, event_id) for event_id, _ in retry],
        )
        if quarantine:
            await self._quarantine(
                db, quarantine, f"attempts_exhausted:{code}", commit=False
            )
        await db.commit()
        return len(quarantine)

    async def _quarantine(
        self,
        db: aiosqlite.Connection,
        rows: Iterable[Any],
        code: str,
        *,
        commit: bool = True,
    ) -> None:
        materialized = list(rows)
        await db.executemany(
            "INSERT OR REPLACE INTO rejected VALUES (?, ?, ?, ?)",
            [
                (event_id, hashlib.sha256(payload.encode()).hexdigest(), payload, code)
                for event_id, payload in materialized
            ],
        )
        await db.executemany(
            "DELETE FROM pending WHERE event_id = ?",
            [(event_id,) for event_id, _ in materialized],
        )
        if commit:
            await db.commit()

    async def _increment_failure(self) -> None:
        self._capture_failures += 1
        try:
            db = await self._open()
            await self._persist_failures(db)
        except Exception:
            logger.exception("accounting loss counter could not be persisted")

    async def record_capture_failure(self) -> None:
        """Record an envelope that could not be made durable by a producer."""
        await self._increment_failure()

    async def _persist_failures(self, db: aiosqlite.Connection) -> None:
        async with self._failure_lock:
            pending = self._capture_failures
            if pending == 0:
                return
            await db.execute(
                "UPDATE health_counters SET value = value + ? WHERE name = 'capture_failures'",
                (pending,),
            )
            await db.commit()
            self._capture_failures -= pending

    async def health(self) -> dict[str, int | float | None]:
        db = await self._open()
        pending = list(await db.execute_fetchall("SELECT COUNT(*) FROM pending"))[0][0]
        rejected = list(await db.execute_fetchall("SELECT COUNT(*) FROM rejected"))[0][
            0
        ]
        failed_delivery = list(
            await db.execute_fetchall("SELECT COUNT(*) FROM pending WHERE attempts > 0")
        )[0][0]
        oldest = list(
            await db.execute_fetchall("SELECT MIN(enqueued_at_ns) FROM pending")
        )[0][0]
        persisted_failures = list(
            await db.execute_fetchall(
                "SELECT value FROM health_counters WHERE name = 'capture_failures'"
            )
        )[0][0]
        return {
            "pending": pending,
            "rejected": rejected,
            "memory_pending": self._queue.qsize(),
            "capture_failures": persisted_failures,
            "failed_delivery": failed_delivery,
            "oldest_pending_seconds": (
                max(0.0, (time.time_ns() - oldest) / 1_000_000_000) if oldest else None
            ),
        }

    async def flush_memory(self) -> None:
        """Wait until every non-blocking capture has reached durable storage."""
        if self._worker is not None:
            await self._queue.join()

    async def aclose(self, *, drain_memory: bool = True) -> None:
        if drain_memory and self._worker is not None:
            await self._queue.join()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        if self._db is not None:
            await self._db.close()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
