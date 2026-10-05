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
