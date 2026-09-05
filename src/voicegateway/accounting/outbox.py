"""Bounded, restart-safe store-and-forward for accounting envelopes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
        memory_queue_size: int = 1_000,
        client: Any | None = None,
    ) -> None:
        self._path = Path(path).expanduser()
        self._url = collector_url.rstrip("/") + "/v1/accounting/usage"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_records = max_records
        self._queue: asyncio.Queue[UsageEnvelope] = asyncio.Queue(memory_queue_size)
        self._client = client
        self._owns_client = client is None
        self._db: aiosqlite.Connection | None = None
        self._worker: asyncio.Task[None] | None = None
        self._capture_failures = 0

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
        return self._db

    async def submit(self, envelope: UsageEnvelope) -> str:
        """Durably enqueue and return ``stored``, ``duplicate``, or ``full``."""
        db = await self._open()
        payload = envelope.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        existing = list(
            await db.execute_fetchall(
                "SELECT payload_hash FROM pending WHERE event_id = ?",
                (envelope.event_id,),
            )
        )
        if existing:
            if existing[0][0] != digest:
                raise ValueError("event identity already queued with different content")
            return "duplicate"
        count = list(await db.execute_fetchall("SELECT COUNT(*) FROM pending"))[0][0]
        if count >= self._max_records:
            self._capture_failures += 1
            return "full"
        await db.execute(
            "INSERT INTO pending(event_id, payload_hash, payload, enqueued_at_ns) VALUES (?, ?, ?, ?)",
            (envelope.event_id, digest, payload, time.time_ns()),
        )
        await db.commit()
        return "stored"

    def enqueue_nowait(self, envelope: UsageEnvelope) -> bool:
        """Non-blocking voice-path capture; False is an explicit loss signal."""
        self.start()
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self._capture_failures += 1
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
                self._capture_failures += 1
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
                await self._mark_retryable(db, rows, f"http_{response.status_code}")
                return {
                    "accepted": 0,
                    "duplicate": 0,
                    "rejected": 0,
                    "retryable": len(rows),
                }
            response.raise_for_status()
            receipts = UsageBatchResponse.model_validate(response.json()).receipts
        except Exception:
            await self._mark_retryable(db, rows, "delivery_error")
            return {
                "accepted": 0,
                "duplicate": 0,
                "rejected": 0,
                "retryable": len(rows),
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
            elif receipt.outcome in {"accepted", "duplicate"}:
                await db.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                counts[receipt.outcome] += 1
            else:
                digest = hashlib.sha256(payload.encode()).hexdigest()
                await db.execute(
                    "INSERT OR REPLACE INTO rejected VALUES (?, ?, ?, ?)",
                    (event_id, digest, payload, receipt.code),
                )
                await db.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                counts["rejected"] += 1
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
                await sleep(min(max_delay, base_delay * (2**attempt)))
        return result

    async def _mark_retryable(
        self, db: aiosqlite.Connection, rows: Iterable[Any], code: str
    ) -> None:
        await db.executemany(
            "UPDATE pending SET attempts = attempts + 1, last_error_code = ? WHERE event_id = ?",
            [(code, event_id) for event_id, _ in rows],
        )
        await db.commit()

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
        return {
            "pending": pending,
            "rejected": rejected,
            "memory_pending": self._queue.qsize(),
            "capture_failures": self._capture_failures,
            "failed_delivery": failed_delivery,
            "oldest_pending_seconds": (
                max(0.0, (time.time_ns() - oldest) / 1_000_000_000) if oldest else None
            ),
        }

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
