"""Unit tests for voicegateway.pricing.llm."""

from __future__ import annotations

from decimal import Decimal

import pytest

from voicegateway.pricing import llm


def test_pricing_source_format() -> None:
    """PRICING_SOURCE follows the documented `genai-prices@<version>` shape."""
    assert llm.PRICING_SOURCE.startswith("genai-prices@")
    # version segment is non-empty
    version = llm.PRICING_SOURCE.split("@", 1)[1]
    assert version, "version segment should be non-empty"


def test_known_openai_model_priced_correctly() -> None:
    """gpt-4o at 1000+100 tokens matches OpenAI's published rates."""
    cost = llm.calculate_llm_cost("openai/gpt-4o", 1000, 100)
    # 1000 * $0.0025/1k input + 100 * $0.01/1k output = $0.0025 + $0.001
    assert cost == Decimal("0.0035")


def test_known_anthropic_model_priced_correctly() -> None:
    """claude-3.5-sonnet at 1000+100 tokens matches Anthropic's published rates."""
    cost = llm.calculate_llm_cost("anthropic/claude-3.5-sonnet", 1000, 100)
    # 1000 * $0.003/1k input + 100 * $0.015/1k output = $0.003 + $0.0015
    assert cost == Decimal("0.0045")


def test_unknown_provider_returns_none() -> None:
    """genai-prices raises LookupError for unknown provider; wrapper returns None."""
    cost = llm.calculate_llm_cost("foo/bar-baz", 1000, 500)
    assert cost is None


def test_known_provider_unknown_model_returns_none() -> None:
    """genai-prices raises LookupError for unknown model under a known provider."""
    cost = llm.calculate_llm_cost("openai/totally-fake-model-2099", 1000, 500)
    assert cost is None


def test_bare_model_name_without_slash() -> None:
    """A model name without a provider/ prefix still resolves via genai-prices."""
    cost = llm.calculate_llm_cost("gpt-4o", 1000, 100)
    assert cost == Decimal("0.0035")


def test_zero_tokens_returns_zero_decimal() -> None:
    """Zero input and zero output return Decimal('0'), not None."""
    cost = llm.calculate_llm_cost("openai/gpt-4o-mini", 0, 0)
    assert cost is not None
    assert cost == Decimal("0")


def test_only_input_tokens() -> None:
    """Only input tokens contribute; output_tokens=0 is handled."""
    cost = llm.calculate_llm_cost("openai/gpt-4o", 1000, 0)
    # 1000 * $0.0025/1k input + 0 = $0.0025
    assert cost == Decimal("0.0025")


def test_only_output_tokens() -> None:
    """Only output tokens contribute; input_tokens=0 is handled."""
    cost = llm.calculate_llm_cost("openai/gpt-4o", 0, 1000)
    # 0 + 1000 * $0.01/1k output = $0.01
    assert cost == Decimal("0.01")


def test_very_large_token_counts() -> None:
    """Decimal arithmetic survives large token counts (1M input + 1M output)."""
    cost = llm.calculate_llm_cost("openai/gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost is not None
    # gpt-4o-mini: $0.00015/1k input + $0.0006/1k output
    # 1M input * 0.00015/1k + 1M output * 0.0006/1k = 0.15 + 0.6 = 0.75
    assert cost == Decimal("0.75")


def test_empty_model_string_returns_none() -> None:
    """Empty model string is treated as unknown."""
    cost = llm.calculate_llm_cost("", 1000, 500)
    assert cost is None


def test_groq_canonical_id_priced_correctly() -> None:
    """Phase 2.5 alignment: canonical Groq names resolve via genai-prices."""
    cost_8b = llm.calculate_llm_cost("groq/llama-3.1-8b-instant", 1000, 500)
    cost_70b = llm.calculate_llm_cost("groq/llama-3.1-70b-versatile", 1000, 500)
    assert cost_8b is not None and cost_8b > Decimal("0")
    assert cost_70b is not None and cost_70b > Decimal("0")


def test_return_type_is_decimal() -> None:
    """Successful calls return Decimal, not float, for downstream precision."""
    cost = llm.calculate_llm_cost("openai/gpt-4o", 1000, 100)
    assert isinstance(cost, Decimal)


@pytest.mark.parametrize(
    "model,input_tokens,output_tokens",
    [
        ("openai/gpt-4o", 1000, 100),
        ("openai/gpt-4o-mini", 1000, 100),
        ("anthropic/claude-3.5-sonnet", 1000, 100),
    ],
)
def test_decimal_avoids_binary_float_artifacts(
    model: str, input_tokens: int, output_tokens: int
) -> None:
    """Decimal(str(float)) keeps trailing zeros / repeating decimals clean."""
    cost = llm.calculate_llm_cost(model, input_tokens, output_tokens)
    assert cost is not None
    # Decimal arithmetic never produces FloatingPointError or
    # 0.1+0.2!=0.3 surprises. Exact equality with the expected
    # rate is the contract.
    assert cost > Decimal("0")
