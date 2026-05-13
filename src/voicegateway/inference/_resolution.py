"""Parse `provider/model` strings used by the voicegateway.inference module."""

from __future__ import annotations

from voicegateway.core.registry import list_providers


class ModelResolutionError(ValueError):
    """Raised when a model string is malformed or names an unknown provider."""


def resolve_model(model: str) -> tuple[str, str]:
    """Parse `"provider/model"` into ``(provider, model_name)``."""
    if not isinstance(model, str) or not model:
        raise ModelResolutionError("Model string must be a non-empty string")

    if "/" not in model:
        raise ModelResolutionError(
            f"Model '{model}' must be in 'provider/model' format"
        )

    provider, _, model_name = model.partition("/")
    if not provider or not model_name:
        raise ModelResolutionError(
            f"Model '{model}' must be in 'provider/model' format"
        )

    known = list_providers()
    if provider not in known:
        raise ModelResolutionError(
            f"Unknown provider '{provider}'. Supported: {', '.join(known)}"
        )

    return provider, model_name
