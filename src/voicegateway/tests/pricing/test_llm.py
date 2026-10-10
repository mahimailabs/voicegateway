"""Unit tests for voicegateway.inference.pricing.llm."""

from __future__ import annotations

from decimal import Decimal

import pytest

from voicegateway.inference.pricing import llm


@pytest.mark.parametrize(
    "model,input_tokens,output_tokens,expected",
    [
        # gpt-4o: $0.0025/1k input + $0.01/1k output
        ("openai/gpt-4o", 1000, 100, "0.0035"),
        ("openai/gpt-4o", 1000, 0, "0.0025"),
        ("openai/gpt-4o", 0, 1000, "0.01"),
        # bare model name resolves via voice-prices
        ("gpt-4o", 1000, 100, "0.0035"),
        # claude-3.5-sonnet: $0.003/1k input + $0.015/1k output
        ("anthropic/claude-3.5-sonnet", 1000, 100, "0.0045"),
        # gpt-4o-mini at 1M + 1M: 0.15 + 0.6
        ("openai/gpt-4o-mini", 1_000_000, 1_000_000, "0.75"),
        ("openai/gpt-4o-mini", 0, 0, "0"),
    ],
)
def test_llm_cost_is_exact_decimal(
    model: str, input_tokens: int, output_tokens: int, expected: str
) -> None:
    cost = llm.calculate_llm_cost(model, input_tokens, output_tokens)
    assert isinstance(cost, Decimal)
    assert cost == Decimal(expected)


@pytest.mark.parametrize("model", ["foo/bar-baz", "openai/totally-fake-model-2099", ""])
def test_unknown_model_returns_none(model: str) -> None:
    """Unknown provider, unknown model or empty id -> None, never a silent zero."""
    assert llm.calculate_llm_cost(model, 1000, 500) is None


def test_groq_canonical_id_priced() -> None:
    """Canonical Groq names resolve via voice-prices."""
    for model in ("groq/llama-3.1-8b-instant", "groq/llama-3.1-70b-versatile"):
        cost = llm.calculate_llm_cost(model, 1000, 500)
        assert cost is not None and cost > Decimal("0")
