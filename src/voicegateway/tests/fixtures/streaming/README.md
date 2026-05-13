# Streaming cost-accounting fixtures

This directory holds JSON fixtures captured from real provider APIs.
`src/voicegateway/tests/test_streaming_cost_accounting.py` replays each fixture and
asserts:

1. **Unit-count consistency.** The fixture's
   `provider_reported_usage` agrees with the actual contents of
   the recorded `response_stream` (LLM tokens vs the trailing
   ChatCompletion usage; STT `audio_seconds` vs Deepgram's
   `metadata.duration`; TTS `character_count` vs
   `len(request.transcript)`). Catches recorder normalization
   bugs, provider schema drift, and off-by-one errors.
2. **Cost calculation.**
   `voicegateway.pricing.catalog.calculate_cost`, given the
   fixture's `provider_reported_usage`, returns the
   `expected_cost_usd` value baked into the fixture (both
   quantized to 8 decimal places). Catches modality-dispatch and
   pricing-source attribution drift in the catalog facade.
3. **TTFB hook behavior.** For stream-mode fixtures, the
   `Instrumented*` wrapper's TTFB hook produces a `ttfb_ms <
   total_latency_ms` when fired partway through, and falls back
   to `ttfb_ms == total_latency_ms` when never fired. Catches
   modality refactors that forget to wire TTFB.

Note: assertion #1 was originally specified as "wrapper's
accumulated unit count matches `provider_reported_usage`"
(literal stream-replay through the wrapper). The v0.0.4
`_InstrumentedBase` is a transparent proxy with no production
stream interception, so the structural-integrity reformulation
above replaces it. Wiring a production stream interceptor is
filed as v0.0.5+ work in `.agents/TODO-phase3.md` Discovered
Work.

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
    "recorded_by": "tests/fixtures/streaming/record_streaming_fixtures.py",
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

2. Set the API keys for whichever providers you are recording. The
   recorder reads `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, and
   `CARTESIA_API_KEY` (plus optional `CARTESIA_VOICE_ID`):

   ```bash
   export OPENAI_API_KEY=sk-...
   export DEEPGRAM_API_KEY=...
   export CARTESIA_API_KEY=...
   ```

3. Run the recorder script. Both `--record` and `--confirm` are
   required; the cost estimate prints first either way.

   ```bash
   python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
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
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm --all
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
- Default prompt is `DEFAULT_LLM_PROMPT` in the recording script
  ("Hello, how are you? Please respond in one short sentence.")
  with `max_tokens=100` so a one-sentence reply fits.

### Deepgram STT

- The script ships with a small bundled audio sample
  (`src/voicegateway/tests/fixtures/audio/test_sample.wav`, 3-second 8 kHz mono
  16-bit PCM, ~48 KB). The recorder refuses to run if the file
  is missing.
- The fixture's `request` block stores a path reference
  (`audio_path: "tests/fixtures/audio/test_sample.wav"`,
  `audio_size_bytes`, `audio_format`) rather than inlined audio
  bytes; replay tests load the file directly when they need
  bytes.
- Stream mode uses Deepgram's WebSocket transcription API at
  `wss://api.deepgram.com/v1/listen?model=&encoding=linear16&
  sample_rate=8000&channels=1`. Authenticates via the
  `Authorization: Token <key>` header.

### Cartesia TTS

- Requires a `CARTESIA_VOICE_ID` env var or the script's default
  voice (a public Cartesia voice id). Output format is fixed at
  PCM 16-bit, **16 kHz** mono so the fixture's
  `audio_size_bytes` is reproducible across recordings.
- Audio bytes are NOT inlined in the fixture; the
  `response_stream` chunk holds metadata only
  (`audio_size_bytes`, `encoding`, `sample_rate`). TTS billing
  is on input characters, so audio fidelity is not what the
  fixture is validating.
- Default text is `DEFAULT_TTS_TEXT` in the recording script
  ("Hello, this is the canonical voicegateway phase 3
  fixture.", 58 characters). `character_count` =
  `len(DEFAULT_TTS_TEXT)`.
- Stream mode uses Cartesia's WebSocket API
  (`wss://api.cartesia.ai/tts/websocket?cartesia_version=&api_key=`);
  authenticates via query string, not header.

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

## Pointers

- `_schema.py` here defines the `StreamingFixture` Pydantic model.
  Every fixture in this directory must validate against it; the
  loader in `_loader.py` enforces this on load.
- `_loader.py` exports `load_fixture(path)`,
  `discover_fixtures()`, `discover_fixture_paths()`, and
  `parse_fixture_filename()`. The replay tests in
  `src/voicegateway/tests/test_streaming_cost_accounting.py` consume these.
- `src/voicegateway/tests/fixtures/streaming/PLACEHOLDER.md` is the runbook for
  recording the six minimum fixtures. Delete it in the same
  commit that lands them.
- `src/voicegateway/tests/fixtures/streaming/record_streaming_fixtures.py` is the
  recorder. Its module docstring documents usage, env vars, and cost
  expectations.
