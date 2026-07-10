---
title: "Adding a Provider"
description: "Step-by-step guide to implementing a new provider that extends BaseProvider and wires into the VoiceGateway registry."
---

# Adding a Provider

VoiceGateway uses a provider registry pattern that makes adding new providers straightforward. Each provider is a single Python file that extends `BaseProvider`. This guide walks through the full process.

## Prerequisites

- [Development environment](/contributing/development-setup) set up
- Familiarity with the provider's API (SDK, auth, pricing)
- Account with the provider for testing

## 10-step checklist

### 1. Create the provider file

Create `src/voicegateway/providers/<name>_provider.py`. Use an existing provider as a template (for example, `deepgram_provider.py` for STT, `cartesia_provider.py` for TTS).

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
```

### 2. Implement the BaseProvider ABC

The `BaseProvider` abstract class in `src/voicegateway/providers/base.py` requires four methods:

| Method | Purpose | Return type |
|---|---|---|
| `create_stt(model, **kwargs)` | Create an STT plugin instance | LiveKit STT plugin or `None` |
| `create_llm(model, **kwargs)` | Create an LLM plugin instance | LiveKit LLM plugin or `None` |
| `create_tts(model, voice, **kwargs)` | Create a TTS plugin instance | LiveKit TTS plugin or `None` |
| `health_check()` | Verify provider connectivity | `bool` |

For modalities the provider does not support, call `self._unsupported("modality_name")` to raise a clear error.

Pricing is not a provider-level concern. LLM, STT, and TTS rates all resolve via `voice-prices` (see step 4). The pricing wrappers live under `src/voicegateway/inference/pricing/`.

### 3. Register the provider

Add your provider to the registry in `src/voicegateway/core/registry.py`:

```python
_PROVIDER_REGISTRY: dict[str, tuple[str, str]] = {
    # ... existing providers ...
    "<name>": ("voicegateway.providers.<name>_provider", "<Name>Provider"),
}
```

The registry uses lazy imports -- your provider module is only loaded when a user configures it. This means optional dependencies do not break the install.

### 4. Add pricing data

Pricing for every modality (LLM, STT, TTS) resolves through `voice-prices`, so there is no VoiceGateway-side catalog entry to add.

If a pricing call returns `None`, the model is not yet in `voice-prices`. Add it upstream in [voice-prices](https://github.com/mahimailabs/voice-prices) (each entry carries `prices_checked` and `pricing_source_url`), publish a new `voice-prices` version, and bump the pin in `pyproject.toml`. Self-hosted models (`local/*`, `ollama/*`) price at `$0` automatically and need no entry.

See [Refreshing Pricing](/contributing/refreshing-pricing) for the full workflow.

### 5. Add optional dependency

Add a new extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
<name> = ["<sdk-package>>=1.0.0"]

# Update the cloud or local group as appropriate
cloud = ["voicegateway[deepgram,openai,...,<name>]"]
```

### 6. Add fake API key to test fixtures

In `src/voicegateway/tests/conftest.py`, add the key to the `_test_env` fixture:

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

Create `src/voicegateway/tests/test_<name>_provider.py`:

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


def test_pricing_resolves_stt(provider):
    """Pricing for a known STT model resolves to a positive Decimal via the catalog."""
    from voicegateway.inference.pricing import catalog
    cost = catalog.calculate_cost("stt", "<name>/model-name", audio_seconds=60)
    assert cost is not None and cost > 0


# For an LLM provider, dispatch with token kwargs:
#
#     cost = catalog.calculate_cost(
#         "llm", "<name>/model-name", input_tokens=1000, output_tokens=500
#     )
#
# For a TTS provider, use character_count:
#
#     cost = catalog.calculate_cost(
#         "tts", "<name>/model-name", character_count=100
#     )
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
ruff check src/voicegateway/providers/<name>_provider.py

# Type check
mypy -p voicegateway.providers.<name>_provider

# Run your tests
pytest src/voicegateway/tests/providers/test_<name>_provider.py -v

# Run the full suite to check for regressions
pytest
```

### 10. Open a PR

Create a PR with:

- **Title:** `feat(providers): add <Provider Name> support`
- **Description:** what modalities are supported, link to provider docs, pricing source
- **Checklist:** all items from the [contributing guide](/contributing/index)

## Anatomy of an existing provider

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
- [Contributing](/contributing/index)
