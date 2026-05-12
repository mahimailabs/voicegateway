"""Tests for v0.6.0 guardrail policy validation and config parsing."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from voicegateway.core.config import GatewayConfig
from voicegateway.core.guardrail_policy import (
    GUARDRAIL_CATEGORIES,
    GuardrailPolicy,
)


def test_disabled_policy_canonicalizes_all_categories_off() -> None:
    policy = GuardrailPolicy.disabled()

    assert policy.enabled is False
    assert policy.is_active is False
    assert policy.active_categories == {}
    assert policy.to_storage_dict() == {
        "enabled": False,
        "categories": dict.fromkeys(GUARDRAIL_CATEGORIES, "off"),
    }


def test_policy_accepts_minimal_enum_actions() -> None:
    policy = GuardrailPolicy.from_raw(
        {
            "enabled": True,
            "categories": {
                "financial": "block",
                "pii": {"action": "redact"},
                "medical": "off",
            },
        }
    )

    assert policy.is_active is True
    assert policy.active_categories == {"pii": "redact", "financial": "block"}


def test_policy_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError, match="unknown guardrail category"):
        GuardrailPolicy.from_raw({"enabled": True, "categories": {"secrets": "block"}})


def test_policy_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        GuardrailPolicy.from_raw({"enabled": True, "categories": {"pii": "rewrite"}})


def test_gateway_config_loads_project_guardrails(tmp_path) -> None:
    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.dump(
            {
                "providers": {},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {
                    "support": {
                        "name": "Support",
                        "guardrails": {
                            "enabled": True,
                            "categories": {
                                "pii": "redact",
                                "prompt_injection": "block",
                            },
                        },
                    }
                },
            }
        )
    )

    config = GatewayConfig.load(str(path))
    policy = config.projects["support"].guardrails

    assert policy.is_active is True
    assert policy.active_categories == {
        "pii": "redact",
        "prompt_injection": "block",
    }
