---
title: voicegw check
description: Verify metering and storage end to end by driving one synthetic request.
---

# voicegw check

Verify that VoiceGateway's metering and storage pipeline works end to end. `check` drives one synthetic instrumented request and confirms a request row and a session row land in storage. It is framework-agnostic: no providers, no models, no network. Use it as a pre-deploy check after touching `voicegw.yaml`, or when triaging a "the dashboard says zero costs" report.

VoiceGateway meters the native provider instances you build in your agent (via `attach()`), so `check` does not construct providers from config. It proves the path a real `attach()`ed call writes through: cost resolution (voice-prices), the SQLite store, and session correlation.

## Usage

```bash
voicegw check [OPTIONS]
```

`smoke-test` is a hidden, deprecated alias for `check` and behaves identically.

## Options

| Flag | Short | Type | Default | Description |
|---|---|---|---|---|
| `--config` | `-c` | `string` | `null` | Path to `voicegw.yaml`. Auto-discovered if omitted. |
| `--project` | `-p` | `string` | `null` | Project to run against. Falls back to `default_project`, then first project, then `default`. |

## What it checks

1. **Config**: `voicegw.yaml` parses and cost tracking is enabled. `check` needs SQLite storage to verify the write.
2. **Active project**: resolves the same way the gateway does (`--project`, then `default_project`, then first non-default project, then `default`).
3. **Request landed**: one synthetic instrumented request is driven through the metering middleware for a priced model id (`openai/gpt-4o-mini`), and the resulting request row is read back from storage.
4. **Session correlation**: the request correlates to a `sessions` row carrying the modality, `request_count`, and `total_cost_usd`. The session id is created, written, and read back inside a single event loop.

If any check fails, the command exits 1 and the report names the failed checks. On success it exits 0.

Requires the `livekit` runtime (the metering wrapper subclasses the LiveKit plugin types); install `voicegateway[livekit]`. If it is absent, `check` reports a clear message rather than crashing.

## Examples

```bash
voicegw check
voicegw check --config /etc/voicegw/staging.yaml --project tony-pizza
```

## Sample output

```text
                     VoiceGateway check
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check               ┃ Status ┃ Detail                               ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ config              │ PASS   │ ~/.config/voicegateway/voicegw.db    │
│ active project      │ PASS   │ default                              │
│ request landed      │ PASS   │ session vg-... has request_count=1   │
│ session correlation │ PASS   │ vg-...: modalities=llm, requests=1   │
└─────────────────────┴────────┴──────────────────────────────────────┘

All checks passed.
```

## What it does NOT replace

`check` proves the metering + storage path. It does not connect to a LiveKit server, capture real audio, or measure end-to-end latency. For a real run, add `attach()` to your agent and place a call:

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev

export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret

python my_agent.py dev   # your agent calls voicegateway.attach(session, project=...)
```

See [Getting started with attach](/guide/attach) for the pattern.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed. |
| `1` | One or more checks failed, or config could not load. |

## Related

- [`voicegw onboard`](/cli/onboard): the wizard runs `check` as its closing step.
- [`voicegw status`](/cli/status): check which providers are configured and reachable.
- [`voicegw logs`](/cli/logs): inspect stored request records.
- [CLI reference](/cli/index): full list of commands.
