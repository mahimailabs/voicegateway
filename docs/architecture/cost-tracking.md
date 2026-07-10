---
title: Cost Tracking
description: How VoiceGateway computes per-request cost for LLM tokens, STT audio-minutes, and TTS characters using voice-prices, and how that data flows from RequestRecord into SQLite and the dashboard.
---

VoiceGateway records the cost of every request that flows through it: tokens for LLM, audio seconds for STT, characters for TTS. Cost data lands in SQLite alongside latency metrics and is the source of truth for the dashboard, the `voicegw reconcile` command, and per-project budget enforcement.

This page covers the cost-tracking subsystem end-to-end: the pricing layer, the per-request flow, and the substitute-validation strategy that backs the streaming cost accuracy claim.

## Architecture

```mermaid
graph LR
    subgraph Request["Per-request path"]
        WRAP["InstrumentedSTT/LLM/TTS<br/>(transparent proxy)"]
        CT["CostTracker.create_record()"]
        STORE["SQLiteStorage.log_request()"]
        BUDGET["BudgetEnforcer.notify_spend()"]
    end

    subgraph Pricing["Pricing layer (modality dispatch)"]
        FACADE["pricing/catalog.py<br/>calculate_cost()"]
        LLM["llm.py<br/>(voice-prices wrapper)"]
        STT["stt.py<br/>(voice-prices wrapper)"]
        TTS["tts.py<br/>(voice-prices wrapper)"]
    end

    WRAP --> CT
    CT --> FACADE
    FACADE --> LLM
    FACADE --> STT
    FACADE --> TTS
    CT --> STORE
    CT --> BUDGET
```

## Pricing layer

The pricing facade in `src/voicegateway/pricing/catalog.py` exposes two functions:

```python
calculate_cost(
    modality: str,
    model: str,
    *,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    character_count: int = 0,
) -> Decimal | None

pricing_source(modality: str) -> str
```

`calculate_cost` dispatches by modality:

- **LLM** (`modality="llm"`): uses `input_tokens` and `output_tokens`. Routes to `pricing/llm.py`, which wraps `voice-prices`. Returns the voice-prices total.
- **STT** (`modality="stt"`): uses `audio_seconds`. Routes to `pricing/stt.py`, which maps the duration onto a `voice-prices` lookup.
- **TTS** (`modality="tts"`): uses `character_count`. Routes to `pricing/tts.py`, same `voice-prices` pattern as STT.
- **Self-hosted** (`local/*`, `ollama/*`): priced at `$0` by a facade guard, attributed as `voicegateway-local`.

All three modalities return `None` for unknown models (never silent zero), so callers can distinguish "free" from "unknown."

A 60-day staleness gate fails CI when any local-catalog entry's `pricing_source_date` is older than 60 days, forcing a quarterly refresh.

## Per-request flow

Every wrapped request flows through `_InstrumentedBase._log_request`:

1. **Compute total latency** as `now - start_time`.
2. **Compute TTFB** as `first_byte_time - start_time` if the streaming hook fired; otherwise fall back to total latency.
3. **Build a `RequestRecord`** via `CostTracker.create_record(...)`, which calls into the pricing facade and attaches `pricing_source` to the record.
4. **Write to storage** via `SQLiteStorage.log_request(...)`. A failure logs at warning and is swallowed; in-memory accounting must not break because the disk is full.
5. **Notify the budget enforcer** via `CostTracker.notify_spend(...)` so per-project caps stay accurate even during a storage outage.

Each `RequestRecord` carries the same `pricing_source` string the catalog returned, so `voicegw reconcile` can attribute the recorded number to a specific upstream catalog version.

## RequestRecord fields

| Field | Type | Source |
|-------|------|--------|
| `model_id` | `str` | Parsed from the `"provider/model"` string |
| `modality` | `str` | `"llm"`, `"stt"`, or `"tts"` |
| `project` | `str` | `ContextVar` set by `attach()` or `guard()` |
| `tenant_id` | `str \| None` | `ContextVar` set by `attach(tenant_id=...)` |
| `input_units` | `int \| float` | Tokens, audio seconds, or characters |
| `output_units` | `int` | Output tokens (LLM only) |
| `cost_usd` | `Decimal` | Result of `calculate_cost()` |
| `pricing_source` | `str` | `voice-prices@<version>` or `voicegateway-local` |
| `ttfb_ms` | `float` | Time to first byte in milliseconds |
| `total_latency_ms` | `float` | End-to-end latency in milliseconds |
| `timestamp` | `datetime` | UTC time of the request |

## How streaming cost accounting is validated

Streaming is where real-world cost-tracking bugs hide: tokens that double at chunk boundaries, audio-second accumulators that drift, character counts that miss SSML markup. VoiceGateway closes the validation gap without requiring real production traffic.

### The substitute strategy

Rather than relying on production traffic, VoiceGateway records real provider streaming responses once via `src/voicegateway/tests/fixtures/streaming/record_streaming_fixtures.py` and replays them in CI. Each fixture is a JSON file with three load-bearing sections:

- `request`: the literal payload VoiceGateway sent.
- `response_stream`: the chunks the provider returned, with `received_at_ms` timestamps.
- `provider_reported_usage`: the usage block the provider reported at end-of-stream (tokens for LLM, duration for STT, character count for TTS).

The fixture also pins `expected_cost_usd`, computed at recording time by passing `provider_reported_usage` through `calculate_cost`. Quantized to 8 decimal places. This locks the cost math at the recording's price: if a catalog updates later, the fixture's `expected_cost_usd` stays at the price-at-recording. The fixture validates VoiceGateway's math, not "today's price."

Filename convention: `<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json`. The date drives the staleness check.

### What the replay tests assert

`src/voicegateway/tests/test_streaming_cost_accounting.py` parameterizes over every committed fixture and asserts three things per fixture:

1. **Unit-count consistency.** `provider_reported_usage` agrees with the actual contents of `response_stream`. For LLM, normalized `input_tokens` / `output_tokens` / `total_tokens` must equal the values inside the trailing ChatCompletion usage chunk. For STT, `audio_seconds` must equal Deepgram's `metadata.duration`. For TTS, `character_count` must equal `len(request.transcript)`. Catches recorder field-name typos, provider schema drift, and off-by-one normalization.
2. **Cost calculation.** `calculate_cost(provider_reported_usage)` quantized to 8 dp must equal `fixture.expected_cost_usd` quantized to 8 dp. Catches cost-layer regressions (modality-dispatch bugs, pricing-source attribution drift, Decimal precision losses).
3. **TTFB hook behavior** (stream fixtures only). A wrapper that calls `_mark_first_byte` partway through must produce `ttfb_ms < total_latency_ms`. A wrapper that never calls it must produce `ttfb_ms == total_latency_ms` (the documented fallback). Catches modality refactors that forget to wire TTFB.

A separate `src/voicegateway/tests/test_ttfb_hook_coverage.py` runs the TTFB-hook contract against synthetic streams for every modality, gated against `wrap_provider`'s dispatch table so a future modality cannot land without TTFB coverage.

### Honest limits of the substitute strategy

Fixture replay is not a complete substitute for production traffic. It does not catch:

- **Real-time streaming behavior.** Replay is sequential and synchronous. Network jitter, partial chunks split across TCP packets, and out-of-order delivery are not simulated.
- **Provider-side correctness.** If Deepgram's reported usage is off by 0.1 seconds, the fixture accepts that as ground truth. The suite validates VoiceGateway's accounting matches the provider's, not whether the provider is right.
- **Stale fixtures.** Recorded fixtures capture provider behavior at a point in time. If a provider changes its streaming format, the fixture's `response_stream` no longer matches what VoiceGateway would see today. The filename's date convention surfaces staleness; a quarterly refresh task is on the maintenance backlog.
- **End-to-end LiveKit session validation.** The wrappers are tested in isolation, not as part of a real `AgentSession`. Session-level integration testing is deferred.

<Note>
Cost tracking is validated against fixture-recorded provider responses, not against real production traffic. The per-fixture date and provider attribution make the validation surface explicit.
</Note>

## Where to find each piece

| Component | Path |
|-----------|------|
| Pricing facade | `src/voicegateway/pricing/catalog.py` |
| LLM pricing | `src/voicegateway/pricing/llm.py` |
| STT pricing | `src/voicegateway/pricing/stt.py` |
| TTS pricing | `src/voicegateway/pricing/tts.py` |
| CostTracker | `src/voicegateway/middleware/cost_tracker.py` |
| InstrumentedProvider | `src/voicegateway/middleware/instrumented_provider.py` |
| Streaming fixtures | `src/voicegateway/tests/fixtures/streaming/` |
| Replay test suite | `src/voicegateway/tests/test_streaming_cost_accounting.py` |
| TTFB hook coverage | `src/voicegateway/tests/test_ttfb_hook_coverage.py` |
