from __future__ import annotations

from voicegateway.accounting.contracts import OwnershipMode, PricingBindingResponse
from voicegateway.accounting.outbox import AccountingOutbox
from voicegateway.models.request_model import RequestRecord
from voicegateway.services.sinks import AccountingCaptureSink
from voicegateway.tests.accounting.test_outbox import Client


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[RequestRecord] = []

    async def log_request(self, record: RequestRecord) -> None:
        self.records.append(record)

    async def log_turns(self, _rows) -> None:
        return None

    async def log_dead_air(self, _events) -> None:
        return None

    async def log_tool_calls(self, _rows) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _record() -> RequestRecord:
    return RequestRecord(
        id="provider-attempt-1",
        timestamp=1,
        modality="llm",
        model_id="provider/model",
        provider="provider",
        input_units=10,
        output_units=2,
        session_id="session-1",
    )


def _binding(mode: OwnershipMode) -> PricingBindingResponse:
    return PricingBindingResponse(
        binding_id="binding-1",
        project_id="default",
        component="conversation",
        offering="provider/model",
        selling_revision_id="sell-1",
        ownership_mode=mode,
        prepared_at_ns=1,
    )


async def test_capture_sink_wires_stable_usage_to_outbox(tmp_path) -> None:
    client = Client()
    outbox = AccountingOutbox(
        tmp_path / "capture.db", "https://collector.invalid", client=client
    )
    primary = RecordingSink()
    sink = AccountingCaptureSink(
        primary, outbox, producer_id="worker-1", binding=_binding(OwnershipMode.SDK)
    )
    await sink.log_request(_record())
    await sink.flush()
    assert [record.id for record in primary.records] == ["provider-attempt-1"]
    assert client.calls == 1
    assert (await outbox.health())["pending"] == 0
    await outbox.aclose()


async def test_capture_sink_does_not_bill_externally_owned_component(tmp_path) -> None:
    client = Client()
    outbox = AccountingOutbox(
        tmp_path / "external.db", "https://collector.invalid", client=client
    )
    sink = AccountingCaptureSink(
        RecordingSink(),
        outbox,
        producer_id="worker-1",
        binding=_binding(OwnershipMode.EXTERNAL),
    )
    await sink.log_request(_record())
    await sink.flush()
    assert client.calls == 0
    assert (await outbox.health())["pending"] == 0
    await outbox.aclose()


async def test_capture_persistence_failure_is_visible_and_does_not_hide_telemetry(
    tmp_path, monkeypatch
) -> None:
    outbox = AccountingOutbox(
        tmp_path / "failed.db", "https://collector.invalid", client=Client()
    )
    primary = RecordingSink()
    sink = AccountingCaptureSink(
        primary, outbox, producer_id="worker-1", binding=_binding(OwnershipMode.SDK)
    )

    async def fail_submit(_envelope) -> str:
        raise OSError("synthetic local persistence failure")

    monkeypatch.setattr(outbox, "submit", fail_submit)
    await sink.log_request(_record())
    assert [record.id for record in primary.records] == ["provider-attempt-1"]
    assert (await outbox.health())["capture_failures"] == 1
    await outbox.aclose()
