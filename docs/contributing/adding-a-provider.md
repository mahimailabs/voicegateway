---
title: "Adding a Provider"
description: "Provider support is a voice-prices pricing entry, not a VoiceGateway code change. The BaseProvider subclass is a separate, optional path for the health-check surface only."
---
VoiceGateway does not construct STT, LLM, or TTS instances for your agent. You build the native plugin yourself (`livekit.plugins.*` or `pipecat.services.*`) and pass it to [`attach()`](/guide/attach) or [`guard()`](/guide/guard). VoiceGateway meters that instance by its `model_id` string and prices it through [voice-prices](https://github.com/mahimailabs/voice-prices).

That means "adding a provider" almost always means one thing: making sure the `provider/model` id resolves to a price. There is no VoiceGateway provider class to write for this.

## Add or confirm a pricing entry

<Steps>
  <Step title="Check whether the model already prices">
    ```python
    from voicegateway.inference.pricing import catalog

    catalog.calculate_cost("stt", "<provider>/<model>", audio_seconds=60)
    catalog.calculate_cost("llm", "<provider>/<model>", input_tokens=1000, output_tokens=500)
    catalog.calculate_cost("tts", "<provider>/<model>", character_count=1000)
    ```
    `None` means the model is not yet in `voice-prices`. Self-hosted ids (`local/*`, `ollama/*`) always return `Decimal('0')` and need no entry.
  </Step>
  <Step title="Add the model to voice-prices">
    Add the model id, match pattern, and `prices` block in the relevant provider file under [voice-prices](https://github.com/mahimailabs/voice-prices)'s `prices/providers/`. Every entry carries a `prices_checked` date and a `pricing_source_url`. Publish a new `voice-prices` version.
  </Step>
  <Step title="Bump the pin">
    Update the `voice-prices` dependency spec in VoiceGateway's `pyproject.toml` (currently `voice-prices>=0.1.0,<0.2`) to require the new version, then confirm it resolves:

    ```bash
    pytest src/voicegateway/tests/pricing/ -q
    ```
  </Step>
</Steps>

See [Refreshing Pricing](/contributing/refreshing-pricing) for the full workflow, including what to do when a provider changes an existing rate.

## Document it

If the provider is new to VoiceGateway (not just a new model on an existing provider), add it to:

- `docs/guide/what-is-voicegateway.md` (owns the provider list)
- `docs/guide/installation.md` (owns the extras matrix)
- `docs/configuration/providers.md` (per-provider config block)

## Appendix: the BaseProvider health-check path (optional)

This section is unrelated to cost tracking. Skip it unless you specifically want the provider to work with the dashboard's **Test Connection** button, `voicegw doctor`, or the MCP server's admin-only provider tools (`test_provider`, `vg_test_provider_key`, gated behind `VOICEGW_MCP_ADMIN=1`).

Those three surfaces are the only production callers of `BaseProvider`. Its `create_stt()` / `create_llm()` / `create_tts()` methods have no other callers in the codebase; `attach()` and `guard()` never touch a `BaseProvider` instance.

<Steps>
  <Step title="Create the provider file">
    Add `src/voicegateway/inference/providers/<name>_provider.py`, subclassing `BaseProvider` from `src/voicegateway/inference/providers/base_provider.py`. Use an existing provider as a template, for example `anthropic_provider.py`:

    ```python
    from __future__ import annotations

    from typing import Any

    from voicegateway.inference.providers.base_provider import BaseProvider


    class <Name>Provider(BaseProvider):
        def __init__(self, config: dict[str, Any]) -> None:
            self.api_key = config.get("api_key", "")

        def create_stt(self, model: str, **kwargs: Any) -> Any:
            self._unsupported("stt")

        def create_llm(self, model: str, **kwargs: Any) -> Any:
            self._unsupported("llm")

        def create_tts(self, model: str, voice: str | None = None, **kwargs: Any) -> Any:
            self._unsupported("tts")

        async def health_check(self) -> bool:
            """Make a lightweight, authenticated API call and return True/False."""
            ...
    ```

    Call `self._unsupported("<modality>")` for whichever `create_*` methods do not apply. `health_check()` is the only method that runs in production; implement it against a cheap endpoint (see `anthropic_provider.py`'s `GET /v1/models` call for the pattern).
  </Step>
  <Step title="Register it">
    Add an entry to `_PROVIDER_REGISTRY` in `src/voicegateway/core/registry.py`:

    ```python
    "<name>": ("voicegateway.inference.providers.<name>_provider", "<Name>Provider"),
    ```

    The registry lazily imports the module on first `create_provider()` call, so an uninstalled plugin does not break the rest of the install.
  </Step>
  <Step title="Add a fake key to test fixtures">
    In `src/voicegateway/tests/conftest.py`, add `"<NAME>_API_KEY"` to the `_test_env` fixture's key list.
  </Step>
  <Step title="Write a health_check test">
    Follow `src/voicegateway/tests/providers/test_cartesia_health_check.py`: mock `httpx.AsyncClient`, assert `health_check()` returns `True` on 200, `False` on a bad status, missing key, or a network error.
  </Step>
</Steps>

## Registered providers

<Note>
This table is `_PROVIDER_REGISTRY` in `src/voicegateway/core/registry.py`. It governs the health-check surface above, not what `attach()`/`guard()` can meter: those meter any `provider/model` id that resolves in voice-prices, registry membership notwithstanding.
</Note>

| Provider | Module | Class |
|---|---|---|
| openai | `openai_provider` | `OpenAIProvider` |
| deepgram | `deepgram_provider` | `DeepgramProvider` |
| anthropic | `anthropic_provider` | `AnthropicProvider` |
| groq | `groq_provider` | `GroqProvider` |
| cartesia | `cartesia_provider` | `CartesiaProvider` |
| elevenlabs | `elevenlabs_provider` | `ElevenLabsProvider` |
| assemblyai | `assemblyai_provider` | `AssemblyAIProvider` |
| ollama | `ollama_provider` | `OllamaProvider` |
| whisper | `whisper_provider` | `WhisperProvider` |
| kokoro | `kokoro_provider` | `KokoroProvider` |
| piper | `piper_provider` | `PiperProvider` |

## Related pages

- [Refreshing Pricing](/contributing/refreshing-pricing)
- [Testing](/contributing/testing)
- [Development Setup](/contributing/development-setup)
- [Contributing](/contributing/index)
