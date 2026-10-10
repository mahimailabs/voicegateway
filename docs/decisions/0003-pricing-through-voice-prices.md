# 0003: Price every modality through voice-prices

- Status: accepted
- Date: recorded 2026-10-10 (the choice predates this record)

## Context

Voice calls are billed in different units per modality: audio seconds for STT, tokens for LLMs, characters or seconds for TTS. Hand-maintained price tables drift within weeks, and wrong prices are the failure users notice first.

## Options

1. A price table inside VoiceGateway: simple, but it goes stale and every fix needs a release.
2. pydantic/genai-prices: maintained, but covers LLM tokens only.
3. voice-prices, a fork of genai-prices extended to STT and TTS units, released on its own cadence.

## Decision

Option 3. `inference/pricing/` dispatches by modality to voice-prices. The dependency floor moves forward with each data release and stays below 1.

## Consequences

- No price is hardcoded in metering code; new models are a voice-prices entry, not a VoiceGateway change (see contributing/refreshing-pricing.md).
- Self-hosted `local/*` and `ollama/*` models price at $0; a catalog match without a rate is tagged `voice-prices-unrated` instead of guessed.
- CI installs from `uv.lock` so the tested catalog version is the one pinned.
