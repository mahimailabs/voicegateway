---
title: voicegw livekit
description: Diagnostics commands for inspecting agents, measuring end-to-end latency, and probing SFU health against a LiveKit server.
---

# voicegw livekit

Diagnostics for a running LiveKit deployment. Four subcommands cover agent listing, end-to-end latency measurement, SFU health, and an all-in-one check report.

```bash
voicegw livekit <subcommand> [OPTIONS]
```

## Credentials

All four subcommands resolve LiveKit credentials in the same order:

1. **CLI flags**: `--url`, `--api-key`, `--api-secret` (highest priority).
2. **Environment variables**: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
3. **Config file**: a `livekit:` block in `voicegw.yaml` (lowest priority).

If credentials are missing after all three layers, the command exits with an error before making any network calls.

```yaml
# voicegw.yaml
livekit:
  url: wss://my-project.livekit.cloud
  api_key: ${LIVEKIT_API_KEY}
  api_secret: ${LIVEKIT_API_SECRET}
```

---

## voicegw livekit agents

List agents that are currently active in rooms on the LiveKit server.

```bash
voicegw livekit agents [OPTIONS]
```

### What it reports

Queries the LiveKit server API for all active rooms and the participants currently inside them. For each participant identified as an agent (dispatched or joined), the command reports:

| Column | Description |
|---|---|
| **Agent** | Participant identity string. |
| **Room** | Room name the agent is currently in. |
| **State** | `active` or `dispatched`. |
| **Joined** | Timestamp the participant joined. |

### Limitation: idle workers are not shown

The LiveKit server API exposes in-room participants only. Agents that are registered and waiting for dispatch (the idle worker pool) are not returned by any current server API. The command footer notes this gap explicitly. Full worker-pool visibility requires a future heartbeat feature (Phase 2) and is not available today.

### Example output

```
AGENT            ROOM                   STATE       IN-CALL  AGE
agent-7f4a       demo-room              active      1        42s
agent-2c9b       qa-room                dispatched  0        8s

2 agents active in 2 rooms. Idle/registered workers are not reported by LiveKit's server API; run the Phase 2 heartbeat to see the full roster.
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |
| `--json` | flag | off | Emit JSON instead of plain text. |

---

## voicegw livekit latency

Measure end-to-end voice latency by placing real synthetic test calls to each agent.

```bash
voicegw livekit latency [OPTIONS]
```

### What it measures

Phase 1 reports **end-to-end latency only**: the time from the end of the caller's speech to the first reply audio frame received from the agent. This is the number users perceive.

For each probe turn the command:

1. Joins a test room as a synthetic caller.
2. Plays a short utterance and waits for end-of-utterance (EOU).
3. Records the time from speech-end to the first reply audio frame arriving from the agent.

| Metric | Description |
|---|---|
| **E2E latency** | Caller speech-end to first reply audio (seconds). This is the number users perceive. |

### Phase 2 (not yet available)

The latency split across turn-detection, STT, LLM, and TTS is a **Phase 2 capability**. The network leg and the per-component breakdown require agents instrumented with `voicegateway.attach(session)` to emit internal timing spans. That integration is not available in Phase 1.

### Cost warning

**Each probe is a real agent turn.** The agent's STT, LLM, and TTS providers are invoked with live credentials and will incur real provider charges. Run with a low `--trials` value (`1` or `2`) unless you are deliberately benchmarking. Keep `--agent` scoped to avoid probing every agent.

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--agent` | `string` | all agents | Probe only the named agent identity. |
| `--trials` | `integer` | `3` | Number of probe turns per agent. |
| `--warmup/--no-warmup` | flag | warmup on | Discard first trial as cold-start warmup. |
| `--target-ms` | `integer` | `1500` | Mark agent SLOW if avg E2E exceeds this threshold (ms). |
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |

### Example output

```
agent-7f4a     E2E avg 0.82s  p50 0.82s  p95 0.84s   GOOD (<1.5s)
  breakdown (turn-detect/STT/LLM/TTS) lands in Phase 2 (collector correlation)
agent-2c9b     E2E avg 1.14s  p50 1.14s  p95 1.18s   SLOW (<1.5s)
  breakdown (turn-detect/STT/LLM/TTS) lands in Phase 2 (collector correlation)
```

---

## voicegw livekit sfu

Measure SFU connection quality from the host running `voicegw`.

```bash
voicegw livekit sfu [OPTIONS]
```

### What it measures

Baseline mode (no flags):

- Connects to the LiveKit SFU and sends data-channel pings.
- Reports round-trip time (RTT) and the LiveKit connection quality score.
- Runs from wherever `voicegw` is executing. If that host is co-located with the SFU (the typical self-hosted setup), the result represents the real agent-to-SFU signal.

Load-ramp mode (`--load`):

- Ramps concurrent prober connections through the levels in `--ramp`.
- At each concurrency level, runs for `--duration` and records RTT and quality score.
- Identifies the capacity knee: the concurrency level at which RTT degrades or quality drops.
- A resource monitor watches CPU and memory on the prober host. If the host itself saturates during the ramp, the output flags this so results are not mistaken for SFU limits.

### Limitations

**Single vantage point.** The prober runs from one host. It does not simulate geo-distributed users. Latency for remote users may differ significantly.

**Prober host saturation.** Under high `--ramp` concurrency, the machine running `voicegw` can become the bottleneck before the SFU does. The resource monitor flags CPU or memory saturation in the output so you can distinguish host limits from SFU limits.

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--load` | flag | off | Enable concurrency ramp mode. |
| `--ramp` | `string` | `2,10,25,50` | Comma-separated concurrency levels for the ramp. |
| `--duration` | `string` | `20s` | How long to hold each concurrency level. |
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |

### Example: baseline

```bash
voicegw livekit sfu
```

```
SFU  vantage: co-located   baseline: rtt 11ms . loss 0.0% . Excellent
```

### Example: load ramp

```bash
voicegw livekit sfu --load --ramp 2,10,25,50 --duration 20s
```

```
SFU  vantage: co-located   baseline: rtt 11ms . loss 0.0% . Excellent
  ramp: 2-> 11ms 0.0% . 10-> 12ms 0.0% . 25-> 18ms 0.1% . 50-> 41ms 1.2%   knee ~25 clients
  prober: ~12% CPU + ~80 kbps up per client; host sustains ~40 before CPU-bound
```

---

## voicegw livekit check

Run all three diagnostics and print a single pass/warn/fail report.

```bash
voicegw livekit check [OPTIONS]
```

### What it runs

Executes `agents`, `latency` (two trials per agent), and `sfu` (baseline) in sequence. For each item it assigns a status:

| Status | Meaning |
|---|---|
| **PASS** | Metric within acceptable range. |
| **WARN** | Metric degraded but not failing (e.g. latency above `--target-ms`). |
| **FAIL** | Error, unreachable, or hard threshold exceeded. |

The command exits 0 if everything passes, 1 if any item is WARN or FAIL.

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--target-ms` | `integer` | `1500` | Latency threshold (ms) for the WARN boundary. |
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |
| `--json` | flag | off | Emit a structured JSON record instead of plain text. |

### Example: plain text output

```bash
voicegw livekit check --target-ms 1000
```

```
VERDICT: WARN

AGENT            ROOM                   STATE       IN-CALL  AGE
agent-7f4a       demo-room              active      1        42s
agent-2c9b       qa-room                dispatched  0        8s

2 agents active in 2 rooms. Idle/registered workers are not reported by LiveKit's server API; run the Phase 2 heartbeat to see the full roster.

agent-7f4a     E2E avg 0.82s  p50 0.82s  p95 0.84s   GOOD (<1.0s)
  breakdown (turn-detect/STT/LLM/TTS) lands in Phase 2 (collector correlation)
agent-2c9b     E2E avg 1.14s  p50 1.14s  p95 1.18s   SLOW (<1.0s)
  breakdown (turn-detect/STT/LLM/TTS) lands in Phase 2 (collector correlation)

SFU  vantage: co-located   baseline: rtt 11ms . loss 0.0% . Excellent
```

### Example: JSON output

```bash
voicegw livekit check --json
```

```json
{
  "agents": [
    {"agent_name": "agent-7f4a", "room": "demo-room", "identity": "agent-7f4a", "state": "active", "humans": 1, "age_s": 42.0},
    {"agent_name": "agent-2c9b", "room": "qa-room", "identity": "agent-2c9b", "state": "dispatched", "humans": 0, "age_s": 8.0}
  ],
  "latency": [
    {"agent": "agent-7f4a", "stats": {"avg": 0.82, "p50": 0.82, "p95": 0.84, "min": 0.80, "max": 0.84, "trials": 2}, "components": null},
    {"agent": "agent-2c9b", "stats": {"avg": 1.14, "p50": 1.14, "p95": 1.18, "min": 1.10, "max": 1.18, "trials": 2}, "components": null}
  ],
  "sfu": {
    "baseline": {"clients": 1, "rtt_ms": 11.0, "loss_pct": 0.0, "quality": "Excellent"},
    "ramp": [],
    "knee": null
  },
  "verdict": "WARN"
}
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed. |
| `1` | One or more checks are WARN or FAIL, or credentials were not resolved. |

---

## Shared limitations

The following limitations apply across all four subcommands:

**In-room agents only.** The LiveKit server API does not expose idle (pre-dispatch) workers. `agents` and `latency` see only agents currently in rooms.

**Real provider cost on latency probes.** Every `latency` probe invokes the agent's actual STT, LLM, and TTS pipeline. Charges are incurred. Use low `--trials` counts for routine checks.

**Per-component latency breakdown is Phase 2.** The split across turn-detection, STT, LLM, and TTS requires agents instrumented with `voicegateway.attach(session)`. Phase 1 reports E2E latency only; the network leg and per-component breakdown are not yet available.

**Single co-located vantage.** `sfu` measures from the host running `voicegw`. This is the correct signal for a self-hosted setup where the gateway and SFU share the same network, but it does not represent latency for end users in other regions.

**Prober host saturation.** During `sfu --load`, the prober machine can saturate before the SFU does. The resource monitor flags this in the output.

---

## Related commands

- [`voicegw smoke-test`](/cli/smoke-test): validate the inference pipeline without a LiveKit server.
- [`voicegw status`](/cli/status): check provider configuration.
- [`voicegw logs`](/cli/logs): view per-request cost and latency records.
- [`voicegw costs`](/cli/costs): aggregated cost view by provider and project.
