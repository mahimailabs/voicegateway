"""Pricing data for all supported providers."""

PRICING: dict[str, dict[str, dict[str, float]]] = {
    "stt": {
        "deepgram/nova-3": {"per_minute": 0.0043},
        "deepgram/nova-2": {"per_minute": 0.0043},
        "deepgram/flux-general": {"per_minute": 0.0043},
        "assemblyai/universal-2": {"per_minute": 0.005},
        "openai/whisper-1": {"per_minute": 0.006},
        "groq/whisper-large-v3": {"per_minute": 0.0},
        "local/whisper-large-v3": {"per_minute": 0.0},
        "local/whisper-turbo": {"per_minute": 0.0},
        "local/whisper-base": {"per_minute": 0.0},
    },
    "llm": {
        "openai/gpt-4.1-mini": {"input_per_1k": 0.0004, "output_per_1k": 0.0016},
        "openai/gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
        "openai/gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
        "anthropic/claude-3.5-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        "groq/llama-3.1-70b": {"input_per_1k": 0.00059, "output_per_1k": 0.00079},
        "groq/llama-3.1-8b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/qwen2.5:3b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/qwen2.5:7b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/llama3.2:3b": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "ollama/phi4-mini": {"input_per_1k": 0.0, "output_per_1k": 0.0},
    },
    "tts": {
        "cartesia/sonic-3": {"per_character": 0.000065},
        "elevenlabs/eleven_turbo_v2_5": {"per_character": 0.00018},
        "deepgram/aura-2": {"per_character": 0.000065},
        "openai/tts-1": {"per_character": 0.000015},
        "local/kokoro": {"per_character": 0.0},
        "local/piper": {"per_character": 0.0},
    },
}


def get_pricing(model_id: str, modality: str) -> dict[str, float]:
    """Get pricing for a model.

    Returns:
        Pricing dict, or empty dict with zero values if model not in catalog.
    """
    modality_pricing = PRICING.get(modality, {})
    return modality_pricing.get(model_id, {})
