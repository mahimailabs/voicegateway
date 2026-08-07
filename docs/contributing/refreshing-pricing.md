---
title: "Refreshing Model Pricing"
description: "How to update rates in voice-prices and bump the VoiceGateway pin when provider pricing changes or a model is missing."
---
VoiceGateway prices every modality (LLM, STT, and TTS) through
[voice-prices](https://github.com/mahimailabs/voice-prices), a fork of
`pydantic/genai-prices` that covers all three modalities. VoiceGateway no
longer keeps any local rate catalogs: rates, source URLs, and verification
dates all live in `voice-prices`.

The pricing wrappers that call into it are:

- `src/voicegateway/inference/pricing/llm.py`
- `src/voicegateway/inference/pricing/stt.py`
- `src/voicegateway/inference/pricing/tts.py`

Each resolves a `provider/model` id against `voice-prices` and returns the
computed cost. The per-request attribution string is `voice-prices@<version>`
for priced models and `voicegateway-local` for self-hosted (`local/*`,
`ollama/*`) models.

## When a refresh is required

A rate is refreshed when a provider publishes a price change, or when a model
VoiceGateway supports is missing from `voice-prices` (a pricing call returns
`None`). Freshness is owned by `voice-prices`: every model entry there carries
a `prices_checked` date and a `pricing_source_url`, so the verification trail
lives upstream rather than in this repo.

## How to refresh a rate

<Steps>
  <Step title="Confirm the current behaviour from VoiceGateway">
    Use the pricing catalog directly to see what the current rate resolves to:

    ```python
    from voicegateway.inference.pricing import catalog

    catalog.calculate_cost("stt", "deepgram/nova-3", audio_seconds=60)
    catalog.calculate_cost("tts", "openai/tts-1", character_count=1000)
    catalog.calculate_cost("llm", "openai/gpt-4o", input_tokens=1000, output_tokens=100)
    ```
  </Step>
  <Step title="Update the rate in voice-prices">
    Edit the model's entry in the relevant provider file under `prices/providers/`, bump its `prices_checked` date, and confirm `pricing_source_url` still points at the provider's price page. Run the `voice-prices` test suite.
  </Step>
  <Step title="Publish a new voice-prices version">
    Publish a new `voice-prices` version to PyPI.
  </Step>
  <Step title="Bump the pin and run pricing tests">
    Update the pin in VoiceGateway's `pyproject.toml` (`voice-prices>=<new-version>,<0.1`) and re-run the pricing tests:

    ```bash
    pytest src/voicegateway/tests/pricing/ -q
    ```
  </Step>
</Steps>

## Adding a missing model

If a pricing call returns `None` for a model VoiceGateway should support, the
model is not yet in `voice-prices`. Add it upstream (model id, match pattern,
and `prices` block) following the existing entries for that provider, publish a
new `voice-prices` version, and bump the pin. The coverage tests in
`src/voicegateway/tests/pricing/test_stt.py` and `test_tts.py` assert that
every supported cloud model resolves, so a missing model fails CI until it is
added.

## Why pricing lives in voice-prices

`voice-prices` gives LLM, STT, and TTS pricing a single versioned source with a
release cadence VoiceGateway can pin against, instead of a hand-maintained
catalog that drifts. Refreshing a rate is a `voice-prices` release plus a pin
bump, and the attribution string records exactly which `voice-prices` version
priced each request.

## Related pages

- [Adding a Provider](/contributing/adding-a-provider)
- [Contributing](/contributing/index)
