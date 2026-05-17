# `voicegw smoke-test`

Validate the inference pipeline end-to-end without spinning up a LiveKit server. Use this as a pre-deploy check after touching `voicegw.yaml`, after a key rotation, or when triaging a "the dashboard says zero costs" report.

## What it checks

1. **Config**: `voicegw.yaml` parses, cost tracking is enabled (smoke-test needs SQLite to verify storage writes).
2. **Active project**: resolves the same way the inference module does (`--project` flag > `default_project` > first non-default project > `default`).
3. **Inference factories**: for each modality with a configured model and provider key, `voicegateway.inference.STT/LLM/TTS` constructs cleanly. The LiveKit plugin layer is stubbed so the run does not hit any provider API.
4. **Storage path**: the wrapped instance's `_log_request` writes a row to the `requests` table.
5. **Session correlation**: all three wrappers share one `session_id`, the `sessions` row aggregates `modalities`, `started_at`, `ended_at`, `total_cost_usd`, and `request_count`.

If any check fails, the command exits with status 1 and the report names the failed checks. On success it exits 0 with the message:

```text
All structural checks passed. For an actual end-to-end run, wire
voicegateway.inference into a LiveKit AgentSession and connect to a
dev server.
```

## Usage

```bash
voicegw smoke-test                      # uses voicegw.yaml + default_project
voicegw smoke-test --config /etc/voicegw/staging.yaml
voicegw smoke-test --project tony-pizza
voicegw smoke-test --live               # also runs each provider's health_check
```

## `--live` flag

Without `--live` the smoke test is offline-safe: stubbed plugins, no API calls. Pass `--live` to additionally run each configured provider's `health_check()` (the same probe `vg_test_provider_key` runs over MCP). The health check makes one lightweight call per provider (typically a model-list endpoint) and reports `reachable` or the exception.

`--live` requires real provider credentials and network access. Skip it on air-gapped machines or when running pre-deploy in CI.

## What it does NOT replace

`smoke-test` covers every layer of the pipeline up to the LiveKit transport. It does not:

- Connect to a LiveKit server.
- Perform real audio capture or playback.
- Verify end-to-end latency (TTFB across STT → LLM → TTS).

For those, run a minimal agent script (see the README Quick Start)
against a LiveKit dev server with real provider keys:

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev

export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret

python my_agent.py dev   # your AgentSession using voicegateway.inference
```

## Sample output

```text
                            VoiceGateway smoke test
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                ┃ Status ┃ Detail                                       ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ config               │ PASS   │ ~/.config/voicegateway/voicegw.db            │
│ active project       │ PASS   │ tony-pizza                                   │
│ inference.STT        │ PASS   │ wrapped deepgram/nova-3                      │
│ storage.requests/stt │ PASS   │ request row written via wrapper              │
│ inference.LLM        │ PASS   │ wrapped openai/gpt-4o-mini                   │
│ storage.requests/llm │ PASS   │ request row written via wrapper              │
│ inference.TTS        │ PASS   │ wrapped cartesia/sonic-3                     │
│ storage.requests/tts │ PASS   │ request row written via wrapper              │
│ session correlation  │ PASS   │ vg-da51995e-... carries llm,stt,tts;         │
│                      │        │ requests=3, cost=$0.004365.                  │
└──────────────────────┴────────┴──────────────────────────────────────────────┘

All structural checks passed.
```

## Common failures

| Failure | Likely cause |
|---|---|
| `config` FAIL: cost tracking disabled | Enable `cost_tracking.enabled: true` in `voicegw.yaml`. |
| `inference.STT` FAIL: `No API key configured for provider 'deepgram'` | The fail-fast preflight. Set the env var the YAML references, or run `vg_add_provider`. |
| `inference.STT` PASS with "skipped (no model registered)" | The project's available providers cover this modality but no model is in `models:`. Add an entry under `models.stt:` for the relevant provider. |
| `health.<provider>` FAIL with auth error (only with `--live`) | Wrong credentials. Rotate via `vg_set_provider_key` (MCP) or update YAML. |
| `session correlation` FAIL: missing entries in `modalities` | Some modality's `_log_request` call did not write through. Check the row above for the underlying error. |

See [`/cli/`](./) for the full CLI reference.
