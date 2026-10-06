"""Visitor summaries expose measurements, never private request fields."""

from types import SimpleNamespace

from voicegateway.services.inside_call import InsideCall


def test_deduplicates_and_omits_private_fields():
    summary = InsideCall()
    record = SimpleNamespace(
        id="one",
        provider="openai",
        model_id="gpt-4o-mini",
        modality="llm",
        input_units=100,
        output_units=20,
        cost_usd=0.001,
        ttfb_ms=120,
        metadata={"transcript": "private"},
    )
    summary.record(record)
    summary.record(record)
    snapshot = summary.snapshot()
    assert snapshot["services"][0]["input_units"] == 100
    assert snapshot["services"][0]["cost_microusd"] == 1000
    assert "private" not in str(snapshot)
    assert "one" not in str(snapshot)


def test_missing_timing_is_not_zero_and_calls_are_isolated():
    first, second = InsideCall(), InsideCall()
    first.record(
        SimpleNamespace(
            id="a",
            provider="cartesia",
            model_id="sonic-2",
            modality="tts",
            input_units=20,
            output_units=0,
            cost_usd=0,
            ttfb_ms=None,
        )
    )
    assert first.snapshot()["services"][0]["ttfb_ms"] is None
    assert second.snapshot()["services"] == []


def test_realtime_duration_and_incomplete_price_are_visible():
    summary = InsideCall()
    for index in range(2):
        summary.record(
            SimpleNamespace(
                id=str(index),
                provider="openai",
                model_id="openai/gpt-live-1",
                modality="llm",
                input_units=0,
                output_units=0,
                cost_usd=0,
                ttfb_ms=-1,
                metadata={
                    "accounting_realtime_quantities": {
                        "audio_seconds": 5,
                        "secret": 99,
                    },
                    "pricing_complete": False,
                },
            )
        )
    service = summary.snapshot()["services"][0]
    assert service["measurements"] == {"audio_seconds": 10}
    assert service["pricing_complete"] is False
    assert service["ttfb_ms"] is None
    assert "secret" not in str(service)
