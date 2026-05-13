"""Parse `provider/model` strings used by the voicegateway.inference module.

The trailing colon-suffix (language for STT, voice for TTS) is NOT stripped
here: it is modality-specific and handled in `_stt.py` / `_tts.py` so that
LLMs receive their model string verbatim (Ollama tags like ``qwen2.5:3b``
must survive). This mirrors the asymmetry inside livekit.agents.inference,
where STT and TTS call ``_parse_model_string`` but LLM does not.

Example:

    >>> resolve_model("deepgram/flux-general")
    ('deepgram', 'flux-general')
    >>> resolve_model("ollama/qwen2.5:3b")
    ('ollama', 'qwen2.5:3b')
    >>> resolve_model("openai/gpt-4o-mini")
    ('openai', 'gpt-4o-mini')
"""

from __future__ import annotations

from voicegateway.core.registry import list_providers


class ModelResolutionError(ValueError):
    """Raised when a model string is malformed or names an unknown provider."""


def resolve_model(model: str) -> tuple[str, str]:
    """Parse `"provider/model"` into ``(provider, model_name)``.

    Args:
        model: A string in ``"provider/model"`` form. Anything after the
            first ``/`` is treated as the model name verbatim, including
            internal colons (e.g. Ollama tags). Modality-specific colon
            suffixes (STT language, TTS voice) are stripped in the
            factories before calling this helper.

    Returns:
        A 2-tuple ``(provider, model_name)``.

    Raises:
        ModelResolutionError: If ``model`` is empty, missing a slash, has
            an empty provider or model component, or names a provider
            that is not registered in
            ``voicegateway.core.registry._PROVIDER_REGISTRY``.
    """
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
