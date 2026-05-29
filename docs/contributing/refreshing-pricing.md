# Refreshing Model Pricing

VoiceGateway prices every modality (LLM, STT, and TTS) through
[voice-prices](https://github.com/mahimailabs/voice-prices), a fork of
`pydantic/genai-prices` that covers all three modalities. VoiceGateway no
longer keeps any local rate catalogs: rates, source URLs, and verification
dates all live in `voice-prices`.

The wrappers that call into it are:

- `src/voicegateway/pricing/llm.py`
- `src/voicegateway/pricing/stt.py`
- `src/voicegateway/pricing/tts.py`

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

1. Confirm the current behaviour from VoiceGateway:
   ```python
   from voicegateway.inference.pricing import llm, stt, tts

   stt.calculate_stt_cost("deepgram/nova-3", 60)   # one minute
   tts.calculate_tts_cost("openai/tts-1", 1000)    # 1000 characters
   llm.calculate_llm_cost("openai/gpt-4o", 1000, 100)
   ```
2. Update the rate in `voice-prices`: edit the model's entry in the relevant
   provider file under `prices/providers/`, bump its `prices_checked` date, and
   confirm `pricing_source_url` still points at the provider's price page. Run
   the `voice-prices` test suite.
3. Publish a new `voice-prices` version to PyPI.
4. Bump the pin in VoiceGateway's `pyproject.toml`
   (`voice-prices>=<new-version>,<0.1`) and re-run the pricing tests:
   ```bash
   pytest src/voicegateway/tests/pricing/ -q
   ```

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
