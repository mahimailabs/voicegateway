# Cost Tracking

VoiceGateway records the cost of every request that flows through it: tokens for LLM, audio seconds for STT, characters for TTS. Cost data lands in SQLite alongside latency metrics and is the source of truth for the dashboard, the `voicegw reconcile` command, and per-project budget enforcement.

This page covers the cost-tracking subsystem end-to-end: the pricing layer, the per-request flow, and the substitute-validation strategy that backs the streaming cost accuracy claim.

## Architecture

```mermaid
graph LR
    subgraph Request["Per-Request Path"]
        WRAP["InstrumentedSTT/LLM/TTS<br/>(transparent proxy)"]
        CT["CostTracker.create_record()"]
        STORE["SQLiteStorage.log_request()"]
        BUDGET["BudgetEnforcer.notify_spend()"]
    end

    subgraph Pricing["Pricing layer (modality dispatch)"]
        FACADE["voicegateway.pricing.catalog<br/>calculate_cost()"]
        LLM["llm.py<br/>(genai-prices wrapper)"]
        STT["stt.py<br/>(local catalog,<br/>per_minute rates)"]
        TTS["tts.py<br/>(local catalog,<br/>per_character rates)"]
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

The pricing facade in `voicegateway/pricing/catalog.py` exposes two functions:

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

- **LLM** (`modality="llm"`): uses `input_tokens` and `output_tokens`. Routes to `pricing/llm.py`, which wraps `pydantic/genai-prices`. Returns the genai-prices total. `pricing_source("llm")` is `genai-prices@<version>`.
- **STT** (`modality="stt"`): uses `audio_seconds`. Routes to `pricing/stt.py`, which holds a local catalog of `per_minute` rates per model. Each entry carries `pricing_source_date` and `pricing_source_url` for auditability. `pricing_source("stt")` is `voicegateway-catalog@<oldest_date>`.
- **TTS** (`modality="tts"`): uses `character_count`. Routes to `pricing/tts.py`, same local-catalog pattern as STT.

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

## How streaming cost accounting is validated

Streaming is where the real-world cost-tracking bugs hide: tokens that double at chunk boundaries, audio-second accumulators that drift, character counts that miss SSML markup. Phase 3 of v0.0.4 closes the validation gap without requiring real production traffic.

### The substitute strategy

Rather than dogfood the gateway in production and reconcile against provider invoices, VG records real provider streaming responses **once** via `scripts/record_streaming_fixtures.py` and replays them in CI forever. Each fixture is a JSON file with three load-bearing sections:

- `request`: the literal payload VG sent.
- `response_stream`: the chunks the provider returned, with `received_at_ms` timestamps.
- `provider_reported_usage`: the usage block the provider reported at end-of-stream (tokens for LLM, duration for STT, character count for TTS).

The fixture also pins `expected_cost_usd`, computed at recording time by passing `provider_reported_usage` through `voicegateway.pricing.catalog.calculate_cost`. Quantized to 8 decimal places. **This locks the cost math at the recording's price**: if a catalog updates later, the fixture's `expected_cost_usd` stays at the price-at-recording. The fixture validates VG's *math*, not "today's price."

Filename convention is locked at `<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json`. The date drives the staleness check.

### What the replay tests assert

`tests/test_streaming_cost_accounting.py` parameterizes over every committed fixture and asserts three things per fixture:

1. **Unit-count consistency**: `provider_reported_usage` agrees with the actual contents of `response_stream`. For LLM, the normalized `input_tokens` / `output_tokens` / `total_tokens` must equal the values inside the trailing ChatCompletion usage chunk. For STT, `audio_seconds` must equal Deepgram's `metadata.duration`. For TTS, `character_count` must equal `len(request.transcript)`. Catches recorder field-name typos, provider schema drift, and off-by-one normalization.
2. **Cost calculation**: `calculate_cost(provider_reported_usage)` quantized to 8 dp must equal `fixture.expected_cost_usd` quantized to 8 dp. Catches cost-layer regressions (modality-dispatch bugs, pricing-source attribution drift, Decimal precision losses).
3. **TTFB hook behavior** (stream fixtures only): a wrapper that calls `_mark_first_byte` partway through must produce `ttfb_ms < total_latency_ms`. A wrapper that never calls it must produce `ttfb_ms == total_latency_ms` (the documented fallback). Catches modality refactors that forget to wire TTFB.

Plus a separate `tests/test_ttfb_hook_coverage.py` runs the TTFB-hook contract against synthetic streams for every modality, gated against `wrap_provider`'s dispatch table so a future modality cannot land without TTFB coverage.

### Honest limits of the substitute strategy

Fixture replay is not a complete substitute for production traffic. It does **not** catch:

- **Real-time streaming behavior**: replay is sequential and synchronous. We do not simulate network jitter, partial chunks split across TCP packets, or out-of-order delivery.
- **Provider-side correctness**: if Deepgram's reported usage is off by 0.1 seconds, the fixture accepts that as ground truth. The suite validates VG's accounting matches the provider's, not whether the provider is right.
- **Stale fixtures**: recorded fixtures capture provider behavior at a point in time. If a provider changes its streaming format, the fixture's `response_stream` no longer matches what VG would see today. The filename's date convention surfaces staleness; a quarterly refresh task is on the v0.0.5+ backlog.
- **End-to-end LiveKit session validation**: the wrappers are tested in isolation, not as part of a real `AgentSession`. Session-level integration testing is deferred (it sits in the OpenRTC-Python Phase 2 plan).

The CHANGELOG disclosure for v0.0.4 is honest about this: "Cost tracking is validated against fixture-recorded responses, not against real production traffic." Without Phase 3, that line would be a lie; with Phase 3, it is the literal description of what shipped.

### Where to find each piece

- `voicegateway/pricing/catalog.py`, `llm.py`, `stt.py`, `tts.py`: the pricing layer.
- `voicegateway/middleware/cost_tracker.py`: per-request record builder.
- `voicegateway/middleware/instrumented_provider.py`: `_InstrumentedBase` + `wrap_provider` + the TTFB / log_request hooks.
- `tests/fixtures/streaming/`: recorded fixtures, schema, loader.
  - `_schema.py`: `StreamingFixture` Pydantic model.
  - `_loader.py`: `discover_fixtures`, `load_fixture`, filename-decode helper.
  - `README.md`: the fixture format and refresh policy.
  - `PLACEHOLDER.md`: runbook for recording the six minimum fixtures.
- `scripts/record_streaming_fixtures.py`: the dev-only recorder, gated behind `--record` and `--confirm`.
- `scripts/README.md`: cost expectations and operational warnings.
- `tests/test_streaming_cost_accounting.py`: the three-assertion replay suite.
- `tests/test_ttfb_hook_coverage.py`: per-modality TTFB hardening.

For the locked design rationale, see `.agents/v0.0.4-phase3.md` (especially §1 for why the substitute strategy exists, §3 for the JSON schema, and §6 for known risks and mitigations).
