# Streaming cost-accounting fixtures

This directory holds JSON fixtures captured from real provider APIs.
`tests/test_streaming_cost_accounting.py` replays each fixture
through VoiceGateway's `InstrumentedSTT|LLM|TTS` wrappers via HTTP
and WebSocket mocks and asserts that:

1. The wrapper's accumulated unit count matches the
   `provider_reported_usage` block (catches off-by-one,
   double-counting, and missed-markup bugs).
2. `voicegateway.pricing.catalog.calculate_cost` produces the
   `expected_cost_usd` value baked into the fixture (catches
   modality dispatch and pricing-source attribution drift).
3. The TTFB hook fires when the first content chunk arrives, not
   at request issuance (catches modality refactors that forget to
   wire TTFB).

CI never calls real provider APIs. The fixtures committed here are
the source of truth.

## Filename convention

```text
<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json
```

Examples:

- `openai_gpt-4o-mini_llm_batch_2026-05-04.json`
- `openai_gpt-4o-mini_llm_stream_2026-05-04.json`
- `deepgram_nova-3_stt_batch_2026-05-04.json`
- `deepgram_nova-3_stt_stream_2026-05-04.json`
- `cartesia_sonic-3_tts_batch_2026-05-04.json`
- `cartesia_sonic-3_tts_stream_2026-05-04.json`

The date suffix is the day the fixture was recorded. When refreshing
a fixture, drop the old file and re-record with today's date so
`git log` for this directory captures when each rate or response
shape was last verified. **No version suffixes** (`_v2`); the
staleness check parses the date out of the filename.

Slashes and colons in model IDs are flattened to underscores
(e.g. a model `ollama/qwen2.5:3b` becomes `ollama_qwen2.5_3b` in
the filename).

## JSON schema

Every fixture matches the `StreamingFixture` Pydantic model in
`_schema.py`. The shape is locked by `.agents/v0.0.4-phase3.md`
§3.1.

```json
{
  "metadata": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "modality": "llm",
    "mode": "stream",
    "recorded_at": "2026-05-04T14:32:11Z",
    "recorded_by": "scripts/record-streaming-fixtures.py",
    "voicegateway_version": "0.0.3"
  },
  "request": {
    "prompt": "Hello, how are you?",
    "max_tokens": 100,
    "stream": true
  },
  "response_stream": [
    {
      "chunk_index": 0,
      "received_at_ms": 312,
      "data": {}
    },
    {
      "chunk_index": 1,
      "received_at_ms": 348,
      "data": {}
    }
  ],
  "provider_reported_usage": {
    "input_tokens": 14,
    "output_tokens": 23,
    "total_tokens": 37
  },
  "expected_cost_usd": "0.00001235"
}
```

Field reference:

- `metadata.provider`: lowercase provider id (`"openai"`,
  `"deepgram"`, `"cartesia"`).
- `metadata.model`: provider-side model name (no `provider/` prefix).
- `metadata.modality`: one of `"stt"`, `"llm"`, `"tts"`.
- `metadata.mode`: `"batch"` or `"stream"`.
- `metadata.recorded_at`: ISO-8601 UTC timestamp with `Z` suffix.
- `metadata.recorded_by`: tool that produced the fixture (the
  recording script's path).
- `metadata.voicegateway_version`: VG version at recording time
  (informational only; the assertions are version-agnostic).
- `request`: the literal request payload that was sent to the
  provider. Replayed at test time so VG's wrapper sees the same
  input the recorded response was a reply to.
- `response_stream`: ordered list of chunks. Each chunk has
  `chunk_index` (zero-based), `received_at_ms` (relative to
  request start), and `data` (the raw chunk payload as a JSON
  object or base64-encoded bytes for binary modalities).
- `provider_reported_usage`: the usage block the provider sent at
  end-of-stream. **This is the ground truth.** Tests assert that
  VG's accumulator reproduces these numbers. Field names follow
  the provider's own canonical names: LLM (OpenAI/Anthropic) uses
  `input_tokens` / `output_tokens` / `total_tokens`; STT uses
  `audio_seconds`; TTS uses `character_count`.
- `expected_cost_usd`: precomputed at recording time by passing
  the `provider_reported_usage` numbers through
  `voicegateway.pricing.catalog.calculate_cost`. Stored as a
  string (not a float) and quantized to 8 decimal places so
  Decimal comparisons are exact. **This locks the cost math at
  the recording's price**: if `genai-prices` later changes
  prices, the fixture's `expected_cost_usd` stays the same. The
  test validates VG's math, not "today's price."

## How to record a fixture

The recording script is dev-only. It hits real provider APIs and
costs real money (a few cents per fixture, well under $0.50 for
the full 6-fixture run).

1. Install the relevant provider SDKs in your dev venv:

   ```bash
   uv pip install openai deepgram-sdk cartesia
   ```

2. Set the API keys for whichever providers you are recording (see
   `.env.fixtures.example` at the repo root for the variable
   names):

   ```bash
   export OPENAI_API_KEY=sk-...
   export DEEPGRAM_API_KEY=...
   export CARTESIA_API_KEY=...
   ```

3. Run the recorder script. Both `--record` and `--confirm` are
   required; the cost estimate prints first either way.

   ```bash
   python scripts/record-streaming-fixtures.py --record --confirm \
     --provider openai --modality llm --model gpt-4o-mini --mode batch
   ```

   The script writes a JSON fixture into this directory and prints
   the filename.

4. Inspect the fixture; commit it.

   ```bash
   git add tests/fixtures/streaming/openai_gpt-4o-mini_llm_batch_2026-05-04.json
   git commit -m "test(fixtures): record OpenAI gpt-4o-mini batch fixture"
   ```

To record all six fixtures in one go (single `--confirm`,
aggregate cost estimate):

```bash
python scripts/record-streaming-fixtures.py --record --confirm --all
```

## How to refresh a fixture

Refresh a fixture when:

- Provider response shape changes (rare; usually catches up via the
  SDK's own breaking-change cycle).
- A new VG wrapper feature needs a fresh ground-truth.

Steps:

1. Delete the old file:

   ```bash
   git rm tests/fixtures/streaming/<old-fixture>.json
   ```

2. Re-record with today's date (the script picks up `date.today()`
   automatically).

3. Commit both the deletion and the addition in the same commit so
   `git log` for this directory tells the refresh history
   accurately.

Provider pricing changes do **not** invalidate fixtures: each
fixture pins its `expected_cost_usd` to the pricing source at
recording time. The fixture validates VG's math, not the current
price. If you want to validate against a new price, you are
recording a new fixture, not refreshing the old one.

## Per-provider recording notes

### OpenAI LLM

- Use `--model gpt-4o-mini` for the canonical fixture; that is the
  model VG's docs and example.yaml lead with.
- The script passes `stream_options={"include_usage": True}` for
  streaming so the final chunk carries token counts. Without this
  the streaming fixture would lack ground truth.

### Deepgram STT

- The script ships with a small bundled audio sample
  (`tests/fixtures/audio/test_sample.wav`, around 3 seconds, PCM).
- Stream mode uses Deepgram's WebSocket transcription API; replay
  uses an in-process WebSocket mock since `respx` covers HTTP only.

### Cartesia TTS

- Requires a `CARTESIA_VOICE_ID` env var or the script's default
  voice. Output format is fixed to PCM 16-bit, 44.1 kHz mono so
  the fixture is reproducible.
- Stream mode uses Cartesia's WebSocket API; same in-process
  WebSocket mock as Deepgram for replay.

## Why these fixtures are committed

CI runs every push and PR. Hitting real provider APIs in CI would:

- Cost real money on every workflow run.
- Make CI flaky against provider rate limits and outages.
- Require API keys in CI secrets, which raises the
  secret-management surface for any contributor.

Committing fixtures puts the ground-truth responses in the repo
and makes CI deterministic. The `.gitignore` does **not** exclude
this directory; please commit recorded fixtures.

## Phase 3 design context

See `.agents/v0.0.4-phase3.md` (especially §3.1 for the JSON
schema and §3.4 for the replay-mechanism description) for the full
reasoning behind this approach. The fixture-replay substitute for
production dogfooding catches the high-frequency bugs (token
counters off by one in streaming, TTFB hooks fire too late) that
the v0.0.4 audit flagged, without requiring real production
traffic.
