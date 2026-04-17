"""Model router — resolves model IDs to provider instances."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.core.model_id import ModelId
from voicegateway.core.registry import create_provider

if TYPE_CHECKING:
    from voicegateway.core.config import GatewayConfig
    from voicegateway.providers.base import BaseProvider


class ModelNotFoundError(Exception):
    """Raised when a model ID is not found in the configuration."""


class ProviderNotConfiguredError(Exception):
    """Raised when a provider's credentials are missing."""


class Router:
    """Routes model IDs to provider instances.

    Lazily creates provider instances on first use to avoid importing
    plugins that aren't needed.
    """

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._providers: dict[str, BaseProvider] = {}

    def _get_provider(self, provider_name: str) -> BaseProvider:
        """Get or create a provider instance."""
        if provider_name not in self._providers:
            provider_config = self._config.get_provider_config(provider_name)
            self._providers[provider_name] = create_provider(
                provider_name, provider_config
            )
        return self._providers[provider_name]

    def resolve(
        self,
        model_id_str: str,
        modality: str,
        project: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Resolve a model ID to a LiveKit-compatible instance.

        Args:
            model_id_str: Model ID string (e.g., "deepgram/nova-3").
            modality: One of "stt", "llm", "tts".
            project: Optional project ID (currently informational — tagged
                     on requests for cost tracking but not passed to plugins).
            **kwargs: Additional provider-specific options.

        Returns:
            A LiveKit-compatible STT, LLM, or TTS instance.

        Raises:
            ModelNotFoundError: If model is not configured.
            ProviderNotConfiguredError: If provider credentials are missing.
        """
        model_id = ModelId.parse(model_id_str)

        # Look up model in config
        model_config = self._config.get_model_config(modality, model_id.config_key)
        if model_config is None:
            raise ModelNotFoundError(
                f"Model '{model_id.config_key}' not found in {modality} configuration. "
                f"Add it to the 'models.{modality}' section of voicegw.yaml."
            )

        # Get the provider name from model config or the model ID
        provider_name = model_config.get("provider", model_id.provider)

        # Resolve the actual model name to pass to the provider
        actual_model = model_config.get("model", model_id.model)

        # Get voice/variant from model ID or config
        voice = model_id.variant or model_config.get("default_voice")

        # Create the provider instance
        try:
            provider = self._get_provider(provider_name)
        except ImportError as e:
            raise ProviderNotConfiguredError(str(e)) from e

        # project is consumed by the gateway layer; don't forward to plugin kwargs
        kwargs.pop("project", None)

        # Create the appropriate instance
        if modality == "stt":
            return provider.create_stt(model=actual_model, **kwargs)
        elif modality == "llm":
            return provider.create_llm(model=actual_model, **kwargs)
        elif modality == "tts":
            return provider.create_tts(model=actual_model, voice=voice, **kwargs)
        else:
            raise ValueError(f"Unknown modality: {modality}")

    def get_provider_status(self) -> dict[str, dict]:
        """Return status for all initialized providers."""
        status = {}
        for name in self._providers:
            status[name] = {"initialized": True}
        return status
