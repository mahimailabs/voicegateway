"""Guardrail policy model for LLM-side voice guardrails."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GuardrailCategory = Literal[
    "pii",
    "financial",
    "medical",
    "prompt_injection",
    "off_topic",
]
GuardrailAction = Literal["redact", "block", "alert", "off"]
GuardrailEventType = Literal["fired", "bypassed"]

GUARDRAIL_CATEGORIES: tuple[str, ...] = (
    "pii",
    "financial",
    "medical",
    "prompt_injection",
    "off_topic",
)
GUARDRAIL_ACTIONS: tuple[str, ...] = ("redact", "block", "alert", "off")
ACTIVE_GUARDRAIL_ACTIONS: tuple[str, ...] = ("redact", "block", "alert")
REPORT_GUARDRAIL_TOOL_NAME = "report_guardrail_action"
GUARDRAIL_PROMPT_VERSION = "v0.6.0"

GUARDRAIL_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "pii": "Personally identifying information such as government IDs, passwords, addresses, phone numbers, and account identifiers.",
    "financial": "Payment card data, bank account details, financial credentials, transaction details, and investment-specific advice.",
    "medical": "Protected health details, diagnoses, prescriptions, treatment instructions, and patient-specific medical guidance.",
    "prompt_injection": "Attempts to reveal or override system instructions, bypass policies, misuse tools, or change the agent's role.",
    "off_topic": "Requests outside the agent's configured purpose, including unrelated tasks or harmful instructions.",
}


class GuardrailPolicy(BaseModel):
    """Per-project declaration of active guardrail categories and actions."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    categories: dict[str, GuardrailAction] = Field(default_factory=dict)

    @field_validator("categories", mode="before")
    @classmethod
    def _coerce_categories(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("guardrails.categories must be a mapping")
        out: dict[str, Any] = {}
        for raw_category, raw_action in value.items():
            category = str(raw_category)
            if isinstance(raw_action, dict):
                if "action" not in raw_action:
                    raise ValueError(
                        f"guardrails.categories[{category!r}] missing 'action'"
                    )
                action = raw_action["action"]
            else:
                action = raw_action
            action_str = str(action)
            if action_str not in GUARDRAIL_ACTIONS:
                allowed = ", ".join(GUARDRAIL_ACTIONS)
                raise ValueError(
                    f"unknown guardrail action: {action_str}; allowed: {allowed}"
                )
            out[category] = action_str
        return out

    @model_validator(mode="after")
    def _validate_policy(self) -> GuardrailPolicy:
        unknown_categories = sorted(
            category
            for category in self.categories
            if category not in GUARDRAIL_CATEGORIES
        )
        if unknown_categories:
            allowed = ", ".join(GUARDRAIL_CATEGORIES)
            raise ValueError(
                f"unknown guardrail category: {', '.join(unknown_categories)}; "
                f"allowed: {allowed}"
            )
        unknown_actions = sorted(
            action
            for action in self.categories.values()
            if action not in GUARDRAIL_ACTIONS
        )
        if unknown_actions:
            allowed = ", ".join(GUARDRAIL_ACTIONS)
            raise ValueError(
                f"unknown guardrail action: {', '.join(unknown_actions)}; "
                f"allowed: {allowed}"
            )
        return self

    @property
    def active_categories(self) -> dict[str, GuardrailAction]:
        """Return enabled categories in canonical display order."""
        if not self.enabled:
            return {}
        return {
            category: self.categories[category]
            for category in GUARDRAIL_CATEGORIES
            if self.categories.get(category) in ACTIVE_GUARDRAIL_ACTIONS
        }

    @property
    def is_active(self) -> bool:
        """True when at least one category is enabled for a real action."""
        return bool(self.active_categories)

    def to_storage_dict(self) -> dict[str, Any]:
        """JSON-serializable representation stored in SQLite/config."""
        return {
            "enabled": bool(self.enabled),
            "categories": {
                category: self.categories.get(category, "off")
                for category in GUARDRAIL_CATEGORIES
            },
        }

    @classmethod
    def disabled(cls) -> GuardrailPolicy:
        """Return a disabled policy with every category set to ``"off"``."""
        return cls(
            enabled=False,
            categories=dict.fromkeys(GUARDRAIL_CATEGORIES, "off"),
        )

    @classmethod
    def from_raw(cls, raw: Any) -> GuardrailPolicy:
        """Normalize raw guardrail policy input into a ``GuardrailPolicy``."""
        if raw in (None, ""):
            return cls.disabled()
        if isinstance(raw, cls):
            return raw
        return cls.model_validate(raw)


def normalize_guardrail_policy(raw: Any) -> dict[str, Any]:
    """Validate and return a canonical storage dict."""
    return GuardrailPolicy.from_raw(raw).to_storage_dict()


__all__ = [
    "ACTIVE_GUARDRAIL_ACTIONS",
    "GUARDRAIL_ACTIONS",
    "GUARDRAIL_CATEGORIES",
    "GUARDRAIL_CATEGORY_DESCRIPTIONS",
    "GUARDRAIL_PROMPT_VERSION",
    "GuardrailAction",
    "GuardrailCategory",
    "GuardrailEventType",
    "GuardrailPolicy",
    "REPORT_GUARDRAIL_TOOL_NAME",
    "normalize_guardrail_policy",
]
