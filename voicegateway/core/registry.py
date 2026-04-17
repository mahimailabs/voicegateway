"""Provider registry — maps provider names to provider classes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voicegateway.providers.base import BaseProvider


# Maps provider name → (module_path, class_name)
_PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    "openai": ("voicegateway.providers.openai_provider", "OpenAIProvider"),
    "deepgram": ("voicegateway.providers.deepgram_provider", "DeepgramProvider"),
    "cartesia": ("voicegateway.providers.cartesia_provider", "CartesiaProvider"),
    "anthropic": ("voicegateway.providers.anthropic_provider", "AnthropicProvider"),
    "groq": ("voicegateway.providers.groq_provider", "GroqProvider"),
    "elevenlabs": ("voicegateway.providers.elevenlabs_provider", "ElevenLabsProvider"),
    "assemblyai": ("voicegateway.providers.assemblyai_provider", "AssemblyAIProvider"),
    "ollama": ("voicegateway.providers.ollama_provider", "OllamaProvider"),
    "whisper": ("voicegateway.providers.whisper_provider", "WhisperProvider"),
    "kokoro": ("voicegateway.providers.kokoro_provider", "KokoroProvider"),
    "piper": ("voicegateway.providers.piper_provider", "PiperProvider"),
}


def create_provider(provider_name: str, config: dict[str, Any]) -> BaseProvider:
    """Create a provider instance by name.

    Args:
        provider_name: Name of the provider (e.g., "openai", "deepgram").
        config: Provider configuration dict from voicegateway.yaml.

    Returns:
        Initialized provider instance.

    Raises:
        ValueError: If provider name is unknown.
    """
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
