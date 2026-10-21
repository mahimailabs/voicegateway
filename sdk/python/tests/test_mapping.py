"""The JSON pass-through for ChatMessage.metrics and usage models."""

from voicegateway._attach import _json_object


def test_keeps_json_values_and_nested_metadata() -> None:
    report = {
        "llm_node_ttft": 0.43,
        "llm_metadata": {"model_name": "m", "model_provider": "p"},
    }
    assert _json_object(report) == report


def test_drops_values_that_are_not_json() -> None:
    assert _json_object({"a": 1.0, "b": object(), "c": float("nan")}) == {"a": 1.0}


def test_empty_or_missing_is_null() -> None:
    assert _json_object({}) is None
    assert _json_object(None) is None
    assert _json_object({"b": object()}) is None
