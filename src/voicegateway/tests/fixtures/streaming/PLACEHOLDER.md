# Recording Phase 3 streaming fixtures

This file is the runbook for mahimairaja to record the six minimum
Phase 3 fixtures. The recording script hits real provider APIs and
costs real money, so it cannot run inside the Ralph Loop or in CI.
Once the six fixture JSON files land in this directory, the loop
unblocks task 3.4 and the replay tests in
`tests/test_streaming_cost_accounting.py` start asserting against
real provider responses.

Delete this file in the same commit that lands the six fixtures.

## Prerequisites

1. Dev install with provider extras:

   ```bash
   uv pip install -e ".[dev,openai,deepgram,cartesia]"
   ```

2. Provider API keys exported in the shell. Either drop them into a
   gitignored `.env.fixtures` file and source it, or export them
   inline:

   ```bash
   set -a; source .env.fixtures; set +a
   # or
   export OPENAI_API_KEY=sk-...
   export DEEPGRAM_API_KEY=...
   export CARTESIA_API_KEY=...
   ```

3. (Cartesia only) Set a voice ID:

   ```bash
   export CARTESIA_VOICE_ID=<your-voice-id>
   ```

4. Confirm working tree is clean (the script writes new files
   here; you want a clean diff to inspect):

   ```bash
   git status tests/fixtures/streaming/
   ```

## Record all six fixtures in one run

```bash
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm --all
```

Expected output (timing and exact dollar amounts will vary by a
few cents):

```text
[1/6] openai/gpt-4o-mini llm/batch
Recording openai/gpt-4o-mini/llm/batch...
  -> tests/fixtures/streaming/openai_gpt-4o-mini_llm_batch_2026-05-05.json
     expected_cost_usd = $0.00001590
[2/6] openai/gpt-4o-mini llm/stream
Recording openai/gpt-4o-mini/llm/stream...
  -> tests/fixtures/streaming/openai_gpt-4o-mini_llm_stream_2026-05-05.json
     expected_cost_usd = $0.00001590
[3/6] deepgram/nova-3 stt/batch
Recording deepgram/nova-3/stt/batch...
  -> tests/fixtures/streaming/deepgram_nova-3_stt_batch_2026-05-05.json
     expected_cost_usd = $0.00021500
[4/6] deepgram/nova-3 stt/stream
Recording deepgram/nova-3/stt/stream...
  -> tests/fixtures/streaming/deepgram_nova-3_stt_stream_2026-05-05.json
     expected_cost_usd = $0.00021500
[5/6] cartesia/sonic-3 tts/batch
Recording cartesia/sonic-3/tts/batch...
  -> tests/fixtures/streaming/cartesia_sonic-3_tts_batch_2026-05-05.json
     expected_cost_usd = $0.00377000
[6/6] cartesia/sonic-3 tts/stream
Recording cartesia/sonic-3/tts/stream...
  -> tests/fixtures/streaming/cartesia_sonic-3_tts_stream_2026-05-05.json
     expected_cost_usd = $0.00377000

Done. 6 fixtures written under tests/fixtures/streaming.
Inspect with `git diff --stat tests/fixtures/streaming/` and commit when ready.
```

## Record a single fixture (recovery / partial reruns)

If the `--all` run fails partway through (provider outage, rate
limit, transient network blip), re-run the missing entries
individually. The script is idempotent on filename: a successful
recording for the same provider/model/modality/mode/date overwrites
the prior file. Different dates produce different files.

```bash
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider openai --modality llm --model gpt-4o-mini --mode batch
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider openai --modality llm --model gpt-4o-mini --mode stream
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider deepgram --modality stt --model nova-3 --mode batch
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider deepgram --modality stt --model nova-3 --mode stream
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider cartesia --modality tts --model sonic-3 --mode batch
python tests/fixtures/streaming/record_streaming_fixtures.py --record --confirm \
  --provider cartesia --modality tts --model sonic-3 --mode stream
```

## Verifying the fixtures

After recording, validate that each file parses against the
`StreamingFixture` schema (created in 3.2 #4):

```bash
uv run pytest src/voicegateway/tests/fixtures/streaming/ -q
```

This loads every JSON file in this directory through the schema's
`load_fixture()` and fails if any structural field is wrong. It
does NOT run the replay tests yet; those land via
`tests/test_streaming_cost_accounting.py` once the schema and
loader are in place.

Then run the full Phase 3 suite:

```bash
uv run pytest src/voicegateway/tests/middleware/test_streaming_cost_accounting.py -q
```

Six fixtures times three assertions each yields 18 parameterized
test cases. All should pass.

## Committing the fixtures

```bash
git add tests/fixtures/streaming/*.json
git rm tests/fixtures/streaming/PLACEHOLDER.md
git commit -m "test(phase3): record streaming cost-accounting fixtures"
```

Single commit, all six fixtures, with `PLACEHOLDER.md` removed.
That commit unblocks TODO 3.4 and the replay assertions land green
on the next CI run.

## What to do if a recording fails

- **Auth error**: confirm the provider's env var is set in the
  shell that ran the script. The script reads the env at startup;
  exporting after the script is running has no effect.
- **Rate limit / quota**: wait a few seconds and re-run the single
  failing fixture. Tiny prompts rarely trip rate limits but
  Cartesia's free tier has tighter limits than OpenAI.
- **Schema validation failure on the recorded JSON**: the
  provider's response shape changed. File a bug pointing at the
  diff between the response and the `StreamingFixture` schema;
  fix the schema before re-recording.
- **Cost estimate looks wrong**: the script's estimate is from
  Phase 2 pricing data. If actual cost diverges by more than 2x,
  check that no extra prompts were issued by mistake (re-run with
  `--record` (without `--confirm`) to print the per-fixture cost
  estimate and the request identity without hitting the API).

## Why this is gated outside CI / the Ralph loop

The Ralph Loop runs in an environment without API keys and would
either fail noisily on every iteration or, worse, charge real
money. The script enforces both `--record` and `--confirm` so
accidental invocation is impossible: forgetting either flag prints
a help banner and exits 0.

Recording is a one-time human activity per fixture refresh. Phase
3 needs it once at setup; v0.0.5+ adds a quarterly refresh task to
the backlog.
