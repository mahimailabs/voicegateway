---
title: voicegw smoke-test
description: Validate the inference pipeline end-to-end without spinning up a LiveKit server.
---

# voicegw smoke-test

Validate the inference pipeline end-to-end without spinning up a LiveKit server. Use this as a pre-deploy check after touching `voicegw.yaml`, after a key rotation, or when triaging a "the dashboard says zero costs" report.

## Usage

```bash
voicegw smoke-test [OPTIONS]
```

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Project to resolve. Falls back to `default_project`, then first project, then `default`. |
| `--live` | | `boolean` | `false` | Also run each provider's `health_check()` (makes live API calls; requires real credentials). |

## What it checks

1. **Config**: `voicegw.yaml` parses and cost tracking is enabled. The smoke test needs SQLite to verify storage writes.
2. **Active project**: resolves the same way the gateway does. Priority: `--project` flag, then `default_project`, then first non-default project, then `default`.
3. **STT/LLM/TTS factories**: for each modality with a configured model and provider key, the plugin constructs cleanly. The LiveKit plugin layer is stubbed so no provider API is called.
4. **Storage path**: the wrapped instance's `_log_request` writes a row to the `requests` table.
5. **Session correlation**: all three wrappers share one `session_id`. The `sessions` row aggregates `modalities`, `started_at`, `ended_at`, `total_cost_usd`, and `request_count`.

If any check fails, the command exits with status 1 and the report names the failed checks. On success it exits 0 with the message:

```text
All structural checks passed. For an actual end-to-end run, connect a
LiveKit AgentSession to a dev server using your configured providers.
```

## The `--live` flag

Without `--live` the smoke test is offline-safe: stubbed plugins, no API calls. Pass `--live` to additionally run each configured provider's `health_check()` (the same probe that `vg_test_provider_key` runs over MCP). The health check makes one lightweight call per provider (typically a model-list endpoint) and reports `reachable` or the exception.

`--live` requires real provider credentials and network access. Skip it on air-gapped machines or when running pre-deploy in CI.

## What it does NOT replace

`smoke-test` covers every layer of the pipeline up to the LiveKit transport. It does not:

- Connect to a LiveKit server.
- Perform real audio capture or playback.
- Verify end-to-end latency (TTFB across STT to LLM to TTS).

For those, run a minimal agent script against a LiveKit dev server with real provider keys:

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev

export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret

# Run your agent that uses voicegateway attach for cost tracking
python my_agent.py dev
```

See [Getting started with attach](/guide/attach) for the `attach` pattern.

## Examples

### Default run

```bash
voicegw smoke-test
```

### Use a specific config and project

```bash
voicegw smoke-test --config /etc/voicegw/staging.yaml --project tony-pizza
```

### Include live provider health checks

```bash
voicegw smoke-test --live
```

## Sample output

```text
                            VoiceGateway smoke test
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                ┃ Status ┃ Detail                                       ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ config               │ PASS   │ ~/.config/voicegateway/voicegw.db            │
│ active project       │ PASS   │ tony-pizza                                   │
│ stt factory          │ PASS   │ wrapped deepgram/nova-3                      │
│ storage.requests/stt │ PASS   │ request row written via wrapper              │
│ llm factory          │ PASS   │ wrapped openai/gpt-4o-mini                   │
│ storage.requests/llm │ PASS   │ request row written via wrapper              │
│ tts factory          │ PASS   │ wrapped cartesia/sonic-3                     │
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
| `stt factory` FAIL: `No API key configured for provider 'deepgram'` | The fail-fast preflight. Set the env var the YAML references, or run `vg_add_provider`. |
| `stt factory` PASS with "skipped (no model registered)" | The project's providers cover this modality but no model is listed under `models.stt:`. Add an entry in `voicegw.yaml`. |
| `health.<provider>` FAIL with auth error (only with `--live`) | Wrong credentials. Rotate via `vg_set_provider_key` (MCP) or update YAML. |
| `session correlation` FAIL: missing entries in `modalities` | Some modality's `_log_request` call did not write through. Check the row above for the underlying error. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed. |
| `1` | One or more checks failed, or config could not load. |

## Related

- [`voicegw status`](/cli/status): check which providers are configured and reachable.
- [`voicegw logs`](/cli/logs): inspect stored request records after a test run.
- [CLI reference](/cli/index): full list of commands.
