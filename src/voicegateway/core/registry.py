"""Provider registry — maps provider names to provider classes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voicegateway.inference.providers.base import BaseProvider


_PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    "openai": ("voicegateway.inference.providers.openai_provider", "OpenAIProvider"),
    "deepgram": (
        "voicegateway.inference.providers.deepgram_provider",
        "DeepgramProvider",
    ),
    "cartesia": (
        "voicegateway.inference.providers.cartesia_provider",
        "CartesiaProvider",
    ),
    "anthropic": (
        "voicegateway.inference.providers.anthropic_provider",
        "AnthropicProvider",
    ),
    "groq": ("voicegateway.inference.providers.groq_provider", "GroqProvider"),
    "elevenlabs": (
        "voicegateway.inference.providers.elevenlabs_provider",
        "ElevenLabsProvider",
    ),
    "assemblyai": (
        "voicegateway.inference.providers.assemblyai_provider",
        "AssemblyAIProvider",
    ),
    "ollama": ("voicegateway.inference.providers.ollama_provider", "OllamaProvider"),
    "whisper": ("voicegateway.inference.providers.whisper_provider", "WhisperProvider"),
    "kokoro": ("voicegateway.inference.providers.kokoro_provider", "KokoroProvider"),
    "piper": ("voicegateway.inference.providers.piper_provider", "PiperProvider"),
}


def create_provider(provider_name: str, config: dict[str, Any]) -> BaseProvider:
    """Create a provider instance by name."""
    if provider_name not in _PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown provider '{provider_name}'. "
            f"Available: {', '.join(sorted(_PROVIDER_REGISTRY))}"
        )

    module_path, class_name = _PROVIDER_REGISTRY[provider_name]

    import importlib

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Could not import provider '{provider_name}': {e}. "
            f"Install with: pip install voicegateway[{provider_name}]"
        ) from e

    cls = getattr(module, class_name)
    provider: BaseProvider = cls(config)
    return provider


def list_providers() -> list[str]:
    """Return all registered provider names."""
    return sorted(_PROVIDER_REGISTRY)
