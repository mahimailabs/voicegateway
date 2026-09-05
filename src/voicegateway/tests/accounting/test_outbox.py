from __future__ import annotations

from voicegateway.accounting.outbox import AccountingOutbox
from voicegateway.tests.accounting.test_contracts import usage


class Response:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


class Client:
    def __init__(self, outcome: str = "accepted") -> None:
        self.outcome = outcome
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
            }
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
