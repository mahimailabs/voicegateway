# Streaming cost-accounting fixtures

This directory holds JSON fixtures captured from real provider APIs.
`tests/test_streaming_cost_accounting.py` replays them through
VoiceGateway's `InstrumentedSTT|LLM|TTS` wrappers via HTTP mocking
and asserts that:

1. The wrapper counts the same input/output units the provider
   reported in its final usage block.
2. VG's calculated cost matches `genai-prices` (LLM) or the local
   STT/TTS catalog for the recorded usage.
3. The TTFB hook fires on the first content chunk during streaming,
   not at request issuance.

CI never calls real provider APIs. The fixtures committed here are
the source of truth.

## Fixture filename convention

```
<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json
```

Examples:

- `openai_gpt-4o-mini_llm_batch_2026-05-04.json`
- `openai_gpt-4o-mini_llm_stream_2026-05-04.json`
- `deepgram_nova-3_stt_batch_2026-05-04.json`
- `cartesia_sonic-3_tts_batch_2026-05-04.json`

The date suffix is the day the fixture was recorded. When refreshing
a fixture, drop the old file and re-record with today's date so the
filename history captures when each rate or response shape was last
verified.

Slashes and colons in model IDs are flattened to underscores
(e.g. a model `ollama/qwen2.5:3b` becomes `ollama_qwen2.5_3b` in
the filename).

## Payload shape

Every fixture carries:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "modality": "llm",
  "mode": "stream",
  "recorded_at": "2026-05-04T08:50:00+00:00",
  "request": { ... raw request payload ... }
}
```

`mode == "batch"` adds:

```json
"response": { ... full provider response object ... },
"usage":    { "prompt_tokens": ..., "completion_tokens": ..., ... }
```

`mode == "stream"` adds:

```json
"chunks":   [ ... per-chunk response objects in order ... ],
"usage":    { ... carried on the final chunk for OpenAI/Anthropic LLM,
                  or summed across the stream for STT/TTS where the
                  provider reports it that way ... }
```

The exact `usage` keys come from the provider; replay tests read
them by their canonical names (`prompt_tokens`, `completion_tokens`,
audio duration, character count, etc.).

## How to record a fixture

1. Install the relevant provider SDK in your dev venv:

   ```bash
   uv pip install openai deepgram-sdk cartesia
   ```

2. Set the API keys for whichever providers you are recording (see
   `.env.fixtures.example` at the repo root for the variable names):

   ```bash
   export OPENAI_API_KEY=sk-...
   export DEEPGRAM_API_KEY=...
   export CARTESIA_API_KEY=...
   ```

3. Run the recorder script with `--record`:

   ```bash
   python scripts/record-streaming-fixtures.py --record \
     --provider openai --modality llm --model gpt-4o-mini --mode batch

   python scripts/record-streaming-fixtures.py --record \
     --provider openai --modality llm --model gpt-4o-mini --mode stream
   ```

   The script writes a JSON fixture into this directory and prints
   the filename. **The recorder hits real APIs and costs real money;**
   the prompts are tiny (max ~20 tokens for LLM, a 3-second test
   audio clip for STT, a single sentence for TTS) so each call is
   sub-cent.

4. Inspect the fixture; commit if it looks reasonable.

   ```bash
   git add tests/fixtures/streaming/openai_gpt-4o-mini_llm_batch_2026-05-04.json
   git commit -m "test(fixtures): record OpenAI gpt-4o-mini batch fixture"
   ```

## How to refresh a fixture

Refresh a fixture when:

- Provider pricing changes and the old fixture's `usage` no longer
  multiplies out to the price `genai-prices` (or the local catalog)
  now reports.
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

3. Commit both deletions and additions in the same commit so `git
   log` for this directory tells the refresh history accurately.

## Per-provider recording notes

### OpenAI LLM

- Use `--model gpt-4o-mini` for the canonical fixture; that is the
  model VG's docs and example.yaml lead with.
- The script passes `stream_options={"include_usage": True}` for
  streaming so the final chunk carries token counts. Without this
  the streaming fixture would lack ground truth.

### Deepgram STT

- Implementation pending (3.1 #1 stub). Full recorder lands
  alongside Phase 3.2's "Record Deepgram nova-3 batch + stream
  fixtures" sub-item.
- Will need a small audio sample (PCM/WAV, around 3 seconds)
  checked into this directory.

### Cartesia TTS

- Implementation pending (3.1 #1 stub). Full recorder lands
  alongside Phase 3.2's Cartesia recording sub-item.
- Will need a voice ID (env var `CARTESIA_VOICE_ID` or a default in
  the script) and an output-format spec.

## Why these fixtures are committed

CI runs every push and PR. Hitting real provider APIs in CI would:

- Cost real money on every workflow run.
- Make CI flaky against provider rate limits and outages.
- Require API keys in CI secrets, which raises the secret-management
  surface for any contributor.

Committing fixtures puts the ground-truth responses in the repo and
makes CI deterministic. The `.gitignore` does NOT exclude this
directory; please commit recorded fixtures.

## Phase 3 design context

See `docs/design/v0.1.0.md` §5.2 ("Streaming validation strategy")
for the full reasoning behind this approach. The fixture-replay
substitute for production dogfooding catches the high-frequency
bugs (token counter off by one in streaming, TTFB hooks fire too
late) that the audit identified, without requiring real production
traffic.
