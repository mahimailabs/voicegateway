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
       Active agents (2)
┌────────────────┬───────────┬────────────┬──────────────────┐
│ Agent          │ Room      │ State      │ Joined           │
├────────────────┼───────────┼────────────┼──────────────────┤
│ agent-7f4a     │ demo-room │ active     │ 14:01:32         │
│ agent-2c9b     │ qa-room   │ dispatched │ 14:03:11         │
└────────────────┴───────────┴────────────┴──────────────────┘

Note: idle (pre-dispatch) workers are not reported by the server API.
Full roster requires the Phase 2 heartbeat feature.
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |
| `--json` | flag | off | Emit JSON instead of a table. |

---

## voicegw livekit latency

Measure end-to-end voice latency by placing real synthetic test calls to each agent.

```bash
voicegw livekit latency [OPTIONS]
```

### What it measures

For each probe turn the command:

1. Joins a test room as a synthetic caller.
2. Plays a short utterance and waits for end-of-utterance (EOU).
3. Records the time from speech-end to the first reply audio frame arriving from the agent.

Two numbers are reported per trial:

| Metric | Description |
|---|---|
| **E2E latency** | Caller speech-end to first reply audio (ms). This is the number users perceive. |
| **Network leg** | Round-trip probe to the SFU data channel (ms). Isolates the transport contribution. |

### Cost warning

**Each probe is a real agent turn.** The agent's STT, LLM, and TTS providers are invoked with live credentials and will incur real provider charges. Run with a low `--trials` value (`1` or `2`) unless you are deliberately benchmarking. Keep `--agent` scoped to avoid probing every agent.

### Descoped: per-component breakdown

The latency split across turn-detection, STT, LLM, and TTS is a **Phase 2 capability**. Phase 1 (this release) reports E2E and network only. The component breakdown requires agents instrumented with `voicegateway.attach(session)` to emit internal timing spans; that integration is not available yet.

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--agent` | `string` | all agents | Probe only the named agent identity. |
| `--trials` | `integer` | `3` | Number of probe turns per agent. |
| `--target-ms` | `integer` | `none` | Warn (yellow) if median E2E exceeds this threshold. |
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |
| `--json` | flag | off | Emit JSON instead of a table. |

### Example output

```
       Latency results -- agent-7f4a (3 trials)
┌───────┬──────────────┬──────────────┐
│ Trial │ E2E (ms)     │ Network (ms) │
├───────┼──────────────┼──────────────┤
│ 1     │ 820          │ 12           │
│ 2     │ 794          │ 11           │
│ 3     │ 843          │ 13           │
├───────┼──────────────┼──────────────┤
│ p50   │ 820          │ 12           │
│ p95   │ 843          │ 13           │
└───────┴──────────────┴──────────────┘

Note: each trial invokes the agent's STT/LLM/TTS and incurs real provider cost.
Per-component breakdown (turn-detection + STT/LLM/TTS split) requires Phase 2.
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
| `--json` | flag | off | Emit JSON instead of a table. |

### Example: baseline

```bash
voicegw livekit sfu
```

```
  SFU quality -- wss://my-project.livekit.cloud
  RTT p50: 11ms   RTT p95: 14ms   Quality: Excellent
```

### Example: load ramp

```bash
voicegw livekit sfu --load --ramp 2,10,25,50 --duration 20s
```

```
  SFU load ramp
┌─────────────┬──────────┬──────────┬───────────┬──────────────────┐
│ Concurrency │ RTT p50  │ RTT p95  │ Quality   │ Host resource    │
├─────────────┼──────────┼──────────┼───────────┼──────────────────┤
│ 2           │ 11ms     │ 13ms     │ Excellent │ OK               │
│ 10          │ 12ms     │ 16ms     │ Excellent │ OK               │
│ 25          │ 18ms     │ 29ms     │ Good      │ OK               │
│ 50          │ 41ms     │ 87ms     │ Poor      │ CPU 94% WARNING  │
└─────────────┴──────────┴──────────┴───────────┴──────────────────┘

Knee detected at concurrency 25 (RTT increase + quality drop).
WARNING: host CPU saturated at concurrency 50.
  Results at this level may reflect prober limits, not SFU limits.
  Re-run from a higher-capacity host or reduce --ramp to confirm.
```

---

## voicegw livekit check

Run all three diagnostics and print a single pass/warn/fail report.

```bash
voicegw livekit check [OPTIONS]
```

### What it runs

Executes `agents`, `latency` (one trial per agent), and `sfu` (baseline) in sequence. For each item it assigns a status:

| Status | Meaning |
|---|---|
| **PASS** | Metric within acceptable range. |
| **WARN** | Metric degraded but not failing (e.g. latency above `--target-ms`). |
| **FAIL** | Error, unreachable, or hard threshold exceeded. |

The command exits 0 if everything passes, 1 if any item is WARN or FAIL.

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--target-ms` | `integer` | `none` | Latency threshold for the WARN/FAIL boundary. |
| `--url` | `string` | (see Credentials) | LiveKit server WebSocket URL. |
| `--api-key` | `string` | (see Credentials) | LiveKit API key. |
| `--api-secret` | `string` | (see Credentials) | LiveKit API secret. |
| `--json` | flag | off | Emit a structured JSON record instead of a table. |

### Example: table output

```bash
voicegw livekit check --target-ms 1000
```

```
          VoiceGateway LiveKit check
┌─────────────────────┬────────┬──────────────────────────────┐
│ Check               │ Status │ Detail                       │
├─────────────────────┼────────┼──────────────────────────────┤
│ agents              │ PASS   │ 2 agents in rooms            │
│ latency/agent-7f4a  │ PASS   │ E2E p50 820ms (target 1000)  │
│ latency/agent-2c9b  │ WARN   │ E2E p50 1140ms > target      │
│ sfu/rtt             │ PASS   │ p50 11ms, quality Excellent  │
└─────────────────────┴────────┴──────────────────────────────┘

Exit code 1 (WARN items present).
```

### Example: JSON output

```bash
voicegw livekit check --json
```

```json
{
  "status": "warn",
  "agents": { "status": "pass", "count": 2 },
  "latency": [
    { "agent": "agent-7f4a", "status": "pass", "e2e_p50_ms": 820, "network_p50_ms": 12 },
    { "agent": "agent-2c9b", "status": "warn", "e2e_p50_ms": 1140, "network_p50_ms": 11 }
  ],
  "sfu": { "status": "pass", "rtt_p50_ms": 11, "rtt_p95_ms": 14, "quality": "excellent" }
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

**Per-component latency breakdown is Phase 2.** The split across turn-detection, STT, LLM, and TTS requires agents instrumented with `voicegateway.attach(session)`. This version reports E2E and network latency only.

**Single co-located vantage.** `sfu` measures from the host running `voicegw`. This is the correct signal for a self-hosted setup where the gateway and SFU share the same network, but it does not represent latency for end users in other regions.

**Prober host saturation.** During `sfu --load`, the prober machine can saturate before the SFU does. The resource monitor flags this in the output.

---

## Related commands

- [`voicegw smoke-test`](/cli/smoke-test): validate the inference pipeline without a LiveKit server.
- [`voicegw status`](/cli/status): check provider configuration.
- [`voicegw logs`](/cli/logs): view per-request cost and latency records.
- [`voicegw costs`](/cli/costs): aggregated cost view by provider and project.
