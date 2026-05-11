# `scripts/`

Dev-only helper scripts. None of these run in CI.

## `record_streaming_fixtures.py`

Records the six Phase 3 streaming fixtures used by
`tests/test_streaming_cost_accounting.py`. Hits real provider APIs
(OpenAI, Deepgram, Cartesia) and **costs real money**. Default-deny
gating means you must pass two flags before any network call lands:

```bash
# Recording disabled banner. No API call.
python scripts/record_streaming_fixtures.py

# Cost estimate dry run. No API call.
python scripts/record_streaming_fixtures.py --record --all

# Actual recording. APIs hit, fixtures written, money spent.
python scripts/record_streaming_fixtures.py --record --all --confirm
```

### Cost expectations

| Fixture | Estimated cost (USD) |
|---|---|
| `openai/gpt-4o-mini` LLM batch | ~$0.00003 |
| `openai/gpt-4o-mini` LLM stream | ~$0.00004 |
| `deepgram/nova-3` STT batch | ~$0.00022 |
| `deepgram/nova-3` STT stream | ~$0.00022 |
| `cartesia/sonic-3` TTS batch | ~$0.00620 |
| `cartesia/sonic-3` TTS stream | ~$0.00620 |
| **Total (`--all`)** | **~$0.01291** |

Estimates come from the v0.0.4 pricing catalog at the canonical
prompt and sample lengths. Actual cost is bounded by the
provider's reply length but is consistently sub-cent per fixture.

### Required environment variables

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI LLM recording |
| `DEEPGRAM_API_KEY` | Deepgram STT recording |
| `CARTESIA_API_KEY` | Cartesia TTS recording |
| `CARTESIA_VOICE_ID` (optional) | Override Cartesia voice id |

`.env.fixtures.example` at the repo root documents the full set.
Copy to `.env.fixtures`, fill in keys, and source before running:

```bash
set -a; source .env.fixtures; set +a
```

### One-shot recording (recommended)

```bash
python scripts/record_streaming_fixtures.py --record --all --confirm
```

Records all six fixtures sequentially with a single `--confirm`.
Fail-fast: if the first OpenAI fixture fails (auth, rate limit,
network), the script exits without spending on subsequent
providers. Partial successes leave fixtures committable for what
landed; re-run individual missing identities afterwards.

### Per-fixture recording (recovery)

```bash
python scripts/record_streaming_fixtures.py --record --confirm \
  --provider openai --modality llm --model gpt-4o-mini --mode batch
```

Use this for partial reruns after `--all` fails partway through.
Idempotent on filename: the same provider/model/modality/mode/date
overwrites the prior fixture; different dates produce different
files.

### Output

Each successful recording writes a JSON file:

```text
tests/fixtures/streaming/<provider>_<model>_<modality>_<mode>_<YYYY-MM-DD>.json
```

The script prints the path and the computed `expected_cost_usd`
after each fixture. Filenames follow the locked convention
documented in `tests/fixtures/streaming/README.md`. The fixture
JSON itself validates against `_schema.py`'s `StreamingFixture`
before being written; a structural drift fails recording, not
later replay.

### Warnings

- **CI never runs this script.** Recording is a one-time human
  activity per fixture refresh. The `--record` and `--confirm`
  gates make accidental invocation impossible.
- **Stream recording uses WebSockets.** Deepgram and Cartesia
  stream recorders import `websockets`. Install with
  `uv pip install -e ".[dev]"` plus `uv pip install 'websockets>=13.0'`
  (websockets is a transitive lock entry, not a dev dep).
- **Cartesia authenticates via URL query string.** Cartesia's
  TTS WebSocket takes the API key as a query parameter
  (`api_key=<key>` on `wss://api.cartesia.ai/tts/websocket?...`),
  not a header. That means the real key can land in:
  - HTTP/WS access logs at any proxy your traffic passes through
  - Network tracing tools (tcpdump output, Wireshark captures)
  - Terminal scrollback if you accidentally print the URL
  - Screenshots, screencasts, or shared logs from the recording
    session
  The bundled recorder does not print or log the URL, but if you
  add custom debug output during a recording session, scrub the
  `api_key=` query before sharing or committing anything. The
  fixture JSONs themselves do NOT contain the URL or the key
  (only the request payload), so commits are safe by default.
- **Audio sample is bundled.** STT recording uses
  `tests/fixtures/audio/test_sample.wav` (3-second 8 kHz mono PCM,
  ~48 KB). Do not delete it; the recorder refuses to run without
  it.
- **Cost catalog drift.** Estimates assume the v0.0.4 pricing
  catalog. If you record with a stale catalog, the fixture's
  `expected_cost_usd` is computed at recording time and pinned;
  it will not match a future catalog's prices. That is by design
  (see design §7 "Decisions log").
- **Quarterly refresh is on the v0.0.5+ backlog.** Fixture dates
  are parsed by the staleness check; fixtures more than a quarter
  old are flagged but not auto-refreshed.

### Refreshing a fixture

When a provider response shape changes and the recorded fixture
no longer replays cleanly:

```bash
git rm tests/fixtures/streaming/<old-fixture>.json
python scripts/record_streaming_fixtures.py --record --confirm \
  --provider <p> --modality <m> --model <m> --mode <mode>
git add tests/fixtures/streaming/<new-fixture>.json
git commit -m "test(fixtures): refresh <provider>/<model> <modality>/<mode>"
```

The script picks up `date.today()` automatically, so the new
filename has today's date. Commit deletions and additions
together so the directory's git history accurately reflects when
each fixture was last verified.

### Why this is gated outside CI

- API keys are dev secrets, not CI secrets.
- Cost is per-run; CI runs every push.
- Provider rate limits and outages would make CI flaky.
- The substitute-validation strategy (committed fixtures + replay
  via mocks) is documented in `.agents/v0.0.4-phase3.md` §1.
