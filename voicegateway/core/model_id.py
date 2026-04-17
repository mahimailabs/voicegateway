"""Parse model identifiers in 'provider/model[:variant]' format."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelId:
    """Parsed model identifier.

    Format: "provider/model[:variant]"
    Examples:
        "deepgram/nova-3" → provider="deepgram", model="nova-3", variant=None
        "cartesia/sonic-3:voice_id" → provider="cartesia", model="sonic-3", variant="voice_id"
        "ollama/qwen2.5:3b" → provider="ollama", model="qwen2.5:3b", variant=None
        "local/kokoro:af_heart" → provider="local", model="kokoro", variant="af_heart"
    """

    provider: str
    model: str
    variant: str | None = None

    @classmethod
    def parse(cls, model_id: str) -> ModelId:
        """Parse a model ID string into its components.

        Handles the ambiguity where colons can appear in both model names
        (ollama/qwen2.5:3b) and variants (local/kokoro:af_heart) by checking
        if the model_id is a known model in the config, falling back to
        treating the last colon-separated part as a variant.
        """
        if "/" not in model_id:
            raise ValueError(
                f"Invalid model ID '{model_id}': must be in 'provider/model' format"
            )

        provider, rest = model_id.split("/", 1)

        if not provider or not rest:
            raise ValueError(
                f"Invalid model ID '{model_id}': provider and model must not be empty"
            )

        # For known local providers that use variant for voice selection,
        # split on the first colon after the model name.
        # For ollama models, the colon is part of the model name (e.g., qwen2.5:3b).
        if provider in ("local",):
            if ":" in rest:
                model, variant = rest.split(":", 1)
                return cls(provider=provider, model=model, variant=variant)
            return cls(provider=provider, model=rest)

        # For other providers, treat the full rest as the model name
        # (this preserves ollama/qwen2.5:3b correctly)
        return cls(provider=provider, model=rest)

    @property
    def full_id(self) -> str:
        """Return the canonical model ID string."""
        base = f"{self.provider}/{self.model}"
        if self.variant:
            return f"{base}:{self.variant}"
        return base

    @property
    def config_key(self) -> str:
        """Return the key used to look up this model in config (without variant)."""
        return f"{self.provider}/{self.model}"

    def __str__(self) -> str:
        return self.full_id
