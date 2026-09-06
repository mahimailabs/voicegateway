from __future__ import annotations

import asyncio

import aiosqlite

from voicegateway.accounting.outbox import AccountingOutbox
from voicegateway.tests.accounting.test_contracts import usage


class Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class Client:
    def __init__(self, outcome: str = "accepted", status_code: int = 200) -> None:
        self.outcome = outcome
        self.status_code = status_code
        self.calls = 0

    async def post(self, _url: str, **kwargs) -> Response:
        self.calls += 1
        return Response(
            {
                "receipts": [
                    {
                        "event_id": item["event_id"],
                        "outcome": self.outcome,
                        "receipt_id": "receipt-1"
                        if self.outcome != "rejected"
                        else None,
                        "code": "committed"
                        if self.outcome == "accepted"
                        else "invalid",
                    }
                    for item in kwargs["json"]
                ]
            },
            self.status_code,
        )


async def test_outbox_survives_restart_and_deletes_only_after_receipt(tmp_path) -> None:
    path = tmp_path / "accounting-outbox.db"
    first = AccountingOutbox(path, "https://collector.invalid", client=Client())
    assert await first.submit(usage()) == "stored"
    await first.aclose()

    client = Client()
    restarted = AccountingOutbox(path, "https://collector.invalid", client=client)
    assert (await restarted.health())["pending"] == 1
    assert await restarted.drain() == {
        "accepted": 1,
        "duplicate": 0,
        "rejected": 0,
        "retryable": 0,
    }
    assert (await restarted.health())["pending"] == 0
    await restarted.aclose()


async def test_outbox_conflict_bound_and_rejected_quarantine(tmp_path) -> None:
    path = tmp_path / "bounded.db"
    outbox = AccountingOutbox(
        path, "https://collector.invalid", max_records=1, client=Client("rejected")
    )
    assert await outbox.submit(usage()) == "stored"
    assert await outbox.submit(usage()) == "duplicate"
    assert (
        await outbox.submit(usage(event_id="event-2", attempt_id="attempt-2")) == "full"
    )
    result = await outbox.drain()
    assert result["rejected"] == 1
    health = await outbox.health()
    assert health["pending"] == 0
    assert health["rejected"] == 1
    assert health["memory_pending"] == 0
    assert health["capture_failures"] == 1
    assert health["failed_delivery"] == 0
    assert health["oldest_pending_seconds"] is None
    await outbox.aclose()


async def test_outbox_terminal_422_is_quarantined(tmp_path) -> None:
    outbox = AccountingOutbox(
        tmp_path / "terminal.db",
        "https://collector.invalid",
        client=Client(status_code=422),
    )
    await outbox.submit(usage())
    assert await outbox.drain() == {
        "accepted": 0,
        "duplicate": 0,
        "rejected": 1,
        "retryable": 0,
    }
    assert (await outbox.health())["pending"] == 0
    await outbox.aclose()


async def test_outbox_does_not_delete_on_outcome_without_receipt_id(tmp_path) -> None:
    class MissingReceiptClient(Client):
        async def post(self, url: str, **kwargs) -> Response:
            response = await super().post(url, **kwargs)
            response._body["receipts"][0]["receipt_id"] = None
            return response

    outbox = AccountingOutbox(
        tmp_path / "missing-receipt.db",
        "https://collector.invalid",
        client=MissingReceiptClient(),
    )
    await outbox.submit(usage())
    assert (await outbox.drain())["retryable"] == 1
    assert (await outbox.health())["pending"] == 1
    await outbox.aclose()


async def test_outbox_enforces_byte_bound_and_persists_loss_counter(tmp_path) -> None:
    path = tmp_path / "bytes.db"
    outbox = AccountingOutbox(
        path, "https://collector.invalid", max_bytes=1, client=Client()
    )
    assert await outbox.submit(usage()) == "full"
    assert (await outbox.health())["capture_failures"] == 1
    await outbox.aclose()
    restarted = AccountingOutbox(path, "https://collector.invalid", client=Client())
    assert (await restarted.health())["capture_failures"] == 1
    await restarted.aclose()


async def test_concurrent_submit_is_idempotent(tmp_path) -> None:
    outbox = AccountingOutbox(
        tmp_path / "concurrent.db", "https://collector.invalid", client=Client()
    )
    outcomes = await asyncio.gather(*(outbox.submit(usage()) for _ in range(8)))
    assert outcomes.count("stored") == 1
    assert outcomes.count("duplicate") == 7
    await outbox.aclose()


async def test_memory_queue_saturation_is_visible(tmp_path) -> None:
    outbox = AccountingOutbox(
        tmp_path / "memory.db",
        "https://collector.invalid",
        memory_queue_size=1,
        client=Client(),
    )
    outbox.start = lambda: None  # type: ignore[method-assign]
    assert outbox.enqueue_nowait(usage()) is True
    assert (
        outbox.enqueue_nowait(usage(event_id="event-2", attempt_id="attempt-2"))
        is False
    )
    await asyncio.sleep(0)
    assert (await outbox.health())["capture_failures"] == 1
    await outbox.aclose(drain_memory=False)


async def test_lost_ack_survives_restart_and_duplicate_receipt_drains(tmp_path) -> None:
    class LostAckClient:
        async def post(self, *_args, **_kwargs):
            raise ConnectionError("response lost after collector commit")

    path = tmp_path / "lost-ack.db"
    first = AccountingOutbox(path, "https://collector.invalid", client=LostAckClient())
    await first.submit(usage())
    assert (await first.drain())["retryable"] == 1
    await first.aclose()
    restarted = AccountingOutbox(
        path, "https://collector.invalid", client=Client("duplicate")
    )
    assert (await restarted.drain())["duplicate"] == 1
    assert (await restarted.health())["pending"] == 0
    await restarted.aclose()


async def test_backoff_is_jittered_and_poison_rows_exhaust(tmp_path) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    outbox = AccountingOutbox(
        tmp_path / "retry.db",
        "https://collector.invalid",
        client=Client(status_code=503),
        max_delivery_attempts=2,
    )
    await outbox.submit(usage())
    result = await outbox.drain_with_backoff(
        max_attempts=2,
        base_delay=1,
        sleep=record_sleep,
        jitter=lambda low, high: (low + high) / 2,
    )
    assert delays == [0.75]
    assert result["rejected"] == 1
    assert result["retryable"] == 0
    assert (await outbox.health())["rejected"] == 1
    await outbox.aclose()


async def test_disk_failure_counter_is_flushed_after_storage_recovers(
    tmp_path, monkeypatch
) -> None:
    outbox = AccountingOutbox(
        tmp_path / "recovery.db", "https://collector.invalid", client=Client()
    )
    connect = aiosqlite.connect

    def fail_connect(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(aiosqlite, "connect", fail_connect)
    await outbox._increment_failure()
    monkeypatch.setattr(aiosqlite, "connect", connect)
    assert (await outbox.health())["capture_failures"] == 1
    await outbox.aclose()
