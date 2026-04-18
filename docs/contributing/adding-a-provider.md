# Adding a Provider

VoiceGateway uses a provider registry pattern that makes adding new providers straightforward. Each provider is a single Python file that extends `BaseProvider`. This guide walks through the full process.

## Prerequisites

- [Development environment](/contributing/development-setup) set up
- Familiarity with the provider's API (SDK, auth, pricing)
- Account with the provider for testing

## 10-step checklist

### 1. Create the provider file

Create `voicegateway/providers/<name>_provider.py`. Use an existing provider as a template (e.g., `deepgram_provider.py` for STT, `cartesia_provider.py` for TTS).

```python
"""<Provider Name> provider implementation."""

from __future__ import annotations

from typing import Any

from voicegateway.providers.base import BaseProvider


class <Name>Provider(BaseProvider):
    """<Provider Name> provider for <STT/LLM/TTS>."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._api_key = config.get("api_key", "")
        # Initialize any SDK client here

    def create_stt(self, model: str, **kwargs: Any) -> Any:
        """Create an STT instance, or call self._unsupported('stt')."""
        self._unsupported("stt")

    def create_llm(self, model: str, **kwargs: Any) -> Any:
        """Create an LLM instance, or call self._unsupported('llm')."""
        self._unsupported("llm")

    def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
        """Create a TTS instance, or call self._unsupported('tts')."""
        self._unsupported("tts")

    async def health_check(self) -> bool:
        """Check if the provider is reachable."""
        # Make a lightweight API call to verify connectivity
        return True

    def get_pricing(self, model: str, modality: str) -> dict[str, float]:
        """Return pricing info for a model."""
        from voicegateway.pricing.catalog import get_pricing
        return get_pricing(f"<name>/{model}", modality)
```

### 2. Implement the BaseProvider ABC

The `BaseProvider` abstract class in `voicegateway/providers/base.py` requires five methods:

| Method | Purpose | Return type |
|---|---|---|
| `create_stt(model, **kwargs)` | Create an STT plugin instance | LiveKit STT plugin or `None` |
| `create_llm(model, **kwargs)` | Create an LLM plugin instance | LiveKit LLM plugin or `None` |
| `create_tts(model, voice, **kwargs)` | Create a TTS plugin instance | LiveKit TTS plugin or `None` |
| `health_check()` | Verify provider connectivity | `bool` |
| `get_pricing(model, modality)` | Return per-unit pricing | `dict[str, float]` |

For modalities the provider does not support, call `self._unsupported("modality_name")` to raise a clear error.

### 3. Register the provider

Add your provider to the registry in `voicegateway/core/registry.py`:

```python
_PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    # ... existing providers ...
    "<name>": ("voicegateway.providers.<name>_provider", "<Name>Provider"),
}
```

The registry uses lazy imports -- your provider module is only loaded when a user configures it. This means optional dependencies do not break the install.

### 4. Add pricing data

Add entries to `voicegateway/pricing/catalog.py`:

```python
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "stt": {
        # ... existing entries ...
        "<name>/<model>": {"per_minute": 0.005},
    },
    "llm": {
        "<name>/<model>": {"input_per_1k": 0.001, "output_per_1k": 0.002},
    },
    "tts": {
        "<name>/<model>": {"per_character": 0.0001},
    },
}
```

Use the pricing units that match the provider's billing:
- STT: `per_minute`
- LLM: `input_per_1k` and `output_per_1k` (per 1,000 tokens)
- TTS: `per_character`

### 5. Add optional dependency

Add a new extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
<name> = ["<sdk-package>>=1.0.0"]

# Update the cloud or local group as appropriate
cloud = ["voicegateway[deepgram,openai,...,<name>]"]
```

### 6. Add fake API key to test fixtures

In `tests/conftest.py`, add the key to the `_test_env` fixture:

```python
@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    for key in [
        # ... existing keys ...
        "<NAME>_API_KEY",
    ]:
        monkeypatch.setenv(key, "test-key-value")
```

### 7. Write tests

Create `tests/test_<name>_provider.py`:

```python
"""Tests for the <Name> provider."""

import pytest

from voicegateway.providers.<name>_provider import <Name>Provider


@pytest.fixture
def provider():
    return <Name>Provider({"api_key": "test-key"})


def test_create_stt(provider):
    # Test STT creation or verify it raises NotImplementedError
    ...


def test_create_llm(provider):
    ...


def test_create_tts(provider):
    ...


async def test_health_check(provider):
    # Mock the HTTP call
    ...


def test_get_pricing(provider):
    pricing = provider.get_pricing("model-name", "stt")
    assert "per_minute" in pricing
    assert pricing["per_minute"] > 0
```

See the [testing guide](/contributing/testing) for mock patterns and fixture usage.

### 8. Update documentation

Add the provider to relevant documentation pages:

- `docs/guide/what-is-voicegateway.md` -- provider list
- `docs/guide/installation.md` -- extras table
- `docs/configuration/` -- config example
- `README.md` -- provider count and list

### 9. Test the full flow

```bash
# Lint
ruff check voicegateway/providers/<name>_provider.py

# Type check
mypy voicegateway/providers/<name>_provider.py

# Run your tests
pytest tests/test_<name>_provider.py -v

# Run the full suite to check for regressions
pytest
```

### 10. Open a PR

Create a PR with:

- **Title:** `feat(providers): add <Provider Name> support`
- **Description:** what modalities are supported, link to provider docs, pricing source
- **Checklist:** all items from the [contributing guide](/contributing/#pr-checklist)

## Example: anatomy of an existing provider

Looking at the registry, VoiceGateway ships with these 11 providers:

| Provider | Module | Class | Modalities |
|---|---|---|---|
| openai | `openai_provider` | `OpenAIProvider` | STT, LLM, TTS |
| deepgram | `deepgram_provider` | `DeepgramProvider` | STT, TTS |
| anthropic | `anthropic_provider` | `AnthropicProvider` | LLM |
| groq | `groq_provider` | `GroqProvider` | STT, LLM |
| cartesia | `cartesia_provider` | `CartesiaProvider` | TTS |
| elevenlabs | `elevenlabs_provider` | `ElevenLabsProvider` | TTS |
| assemblyai | `assemblyai_provider` | `AssemblyAIProvider` | STT |
| ollama | `ollama_provider` | `OllamaProvider` | LLM |
| whisper | `whisper_provider` | `WhisperProvider` | STT |
| kokoro | `kokoro_provider` | `KokoroProvider` | TTS |
| piper | `piper_provider` | `PiperProvider` | TTS |

## Related pages

- [Code Style](/contributing/code-style)
- [Testing](/contributing/testing)
- [Development Setup](/contributing/development-setup)
- [Contributing](/contributing/)
