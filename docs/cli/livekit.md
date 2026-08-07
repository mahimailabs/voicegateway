---
title: voicegw livekit
description: "Diagnose a LiveKit deployment: list agents, measure latency, probe SFU health, gate CI, and export a recorded run."
---
Five subcommands against a running LiveKit deployment: `agents`, `latency`, `sfu`, `check`, `report`. For how this fits with the agent and SIP layers, and what a correlated result can and cannot claim, see [What you can profile](/guide/what-you-can-profile). For a live view of the same rooms and agents from a browser, see the dashboard's [Server page](/guide/server-page).

```bash
voicegw livekit <subcommand> [OPTIONS]
```

## Credentials

Every subcommand resolves LiveKit credentials in the same order:

1. **Flags**: `--url`, `--api-key`, `--api-secret`.
2. **Env**: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
3. **Config**: a `livekit:` block in `voicegw.yaml`.

```yaml
livekit:
  url: wss://my-project.livekit.cloud
  api_key: ${LIVEKIT_API_KEY}
  api_secret: ${LIVEKIT_API_SECRET}
```

Missing credentials exit with an error before any network call, except for `report`: it exports a stored run rather than talking to a server, so missing credentials just print "not recorded" in the file instead of blocking the export.

Every subcommand also accepts `--config`/`-c` to point at a specific `voicegw.yaml`.

All five need the `livekit` extra installed (`voicegateway[livekit]`), not just the ones that place calls: the CLI package imports LiveKit at module scope, so even `voicegw --help` fails without it. See [Installation](/guide/installation).

---

## voicegw livekit agents

Lists agents currently active in rooms: identity, room, state (`active` or `dispatched`), joined time. `--json` emits the same data as JSON.

The LiveKit server API only sees in-room participants; idle, pre-dispatch workers are invisible to it. Instrument agents with `voicegateway.register_worker(...)` to close that gap. When `VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY` are set, `agents` also fetches that heartbeat roster and prints it below the in-room table (best-effort: if the collector is unreachable, the in-room table still renders). Without the collector, only the in-room view shows.

---

## voicegw livekit latency

Places real synthetic test calls and measures **end-to-end latency**: the time from the caller's speech ending to the agent's first reply audio frame. This is the number a caller perceives.

<Warning>
Each probe is a real agent turn. STT, LLM, and TTS providers are invoked with live credentials and billed. Keep `--trials` low (1 or 2) and `--agent` scoped unless you are deliberately benchmarking.
</Warning>

When the probed agent runs `voicegateway.attach(session)`, `latency` also reports the split across turn-detection, STT, LLM (time-to-first-token), and TTS, correlated by room name. The read-back is co-located: the agent and the prober must share the same local store (`~/.config/voicegateway/voicegw.db`, or `VOICEGW_DB_PATH`). In collector mode (`VOICEGW_COLLECTOR_URL` set) the rows go to the collector instead, so the CLI cannot show the split.

Options: `--agent` (default: every agent), `--trials` (default `3`), `--warmup/--no-warmup` (default on), `--target-ms` (default `1500`; marks an agent SLOW above this).

---

## voicegw livekit sfu

Measures SFU connection quality from the host running `voicegw`.

**Baseline** (no flags): connects to the SFU, sends data-channel pings, reports round-trip time and LiveKit's connection quality score. If the host is co-located with the SFU, this is the real agent-to-SFU signal.

**Load ramp** (`--load`): ramps concurrent prober connections through `--ramp` (default `2,10,25,50`), holding each level for `--duration` (default `20s`), and reports the concurrency where RTT degrades or quality drops (the capacity knee). A resource monitor watches the prober host's own CPU and memory and flags saturation, so a bottleneck on the prober is not mistaken for one on the SFU.

### Distributed (multi-vantage)

One host only shows what one machine can push. Run one coordinator and N probers to ramp the same room concurrently from several regions:

```bash
# coordinator - needs the dashboard extra: pip install 'voicegateway[dashboard]'
voicegw livekit sfu --coordinator --expect 3 --ramp 10,25,50 --duration 20s

# each prober - needs the livekit extra: pip install 'voicegateway[livekit]'
voicegw livekit sfu --report-to http://<coordinator-host>:8787 --vantage iad
```

Each prober registers, waits at a shared barrier so every vantage starts its ramp at the same instant, ramps the shared room, and reports its per-tier measurements back. The coordinator sums clients per tier and reports the worst rtt/loss/quality any vantage saw.

For deploying probers across regions, including the fix the shipped prober image needs, see [Distributed SFU probers](/deployment/distributed-sfu). For per-node CPU, memory, and file-descriptor metrics scraped during a ramp, see [Node metrics](/guide/node-metrics). `voicegw loadtest` correlates the same node scrape against SIP-side call evidence; see [voicegw loadtest](/cli/loadtest) rather than treating the two as one run.

Options: `--load`, `--ramp`, `--duration`, `--coordinator` (needs `[dashboard]`), `--expect` (probers to wait for), `--coordinator-port` (default `8787`), `--report-to` (prober mode: coordinator URL), `--vantage` (label, default `$VOICEGW_REGION`).

---

## voicegw livekit check

Runs `agents`, `latency` (two trials per agent), and `sfu` baseline in sequence, gates each result, and prints one verdict. Built for CI: exits non-zero unless every gate passed.

<Warning>
`check` runs the same real, billed probe as `latency`, two agent turns per agent invoking live STT/LLM/TTS, but does not print a cost warning when it runs. Treat it exactly like `latency` for cost.
</Warning>

The verdict is the worst gate:

| Status | Meaning |
|---|---|
| **PASS** | Evaluated, within budget. |
| **WARN** | Evaluated, degraded (e.g. latency over `--target-ms`). |
| **UNKNOWN** | Could not be evaluated: no agent probed, or SFU returned no quality. |
| **FAIL** | Errored/timed out, or SFU quality was `Poor`/`Lost`. |

`UNKNOWN` is not a pass: a gate that never ran has not demonstrated anything. Gates: `agents_listing` (the server API answered; does **not** assert any agent is online, since idle workers are invisible to it), `agent_reply_latency` (per agent, within `--target-ms`), `sfu_connection_quality` (baseline not `Poor`/`Lost`). Packet loss is never gated: the LiveKit SDK exposes no per-connection loss, so `loss_pct` is a hardcoded `0.0`.

`--strict` gates the slowest measured turn instead of the average, named `agent_reply_latency_max_of_2_ms` (never `p95`; that needs 10+ samples).

Options: `--strict`, `--target-ms` (default `1500`), `--json`.

Exit codes: `0` only when every gate was evaluated and passed. `1` for any WARN, UNKNOWN, or FAIL, a crash, or unresolved credentials.

<Warning>
**Exit codes changed.** `check` used to report PASS for a run that measured nothing, most notably when no agent was in any room and the latency gate never ran. That case is now UNKNOWN and exits 1. A pipeline that passed on that condition before will start failing. That is the gate working, not a regression.
</Warning>

---

## voicegw livekit report

Exports a diagnostics run the dashboard already recorded as one self-contained HTML file, rendered through the same code path the dashboard's own download button uses: byte-identical to `GET /api/diagnostics/runs/{id}/report.html` for the same run.

Probes nothing. No call is placed, nothing is billed, and the verdict in the file is whatever the run recorded, not a fresh judgement. The document carries its own CSS inline and loads nothing external, so it renders identically from `file://` with no internet.

A run whose checks never completed still exports: every check states whether it was never requested, requested but never recorded, or errored. No unmeasured value renders as `0`; `--json` reports the same absences as `null`.

Options: `--run` (default: most recent), `--out` (default `./voicegateway-diagnostics-<run>.html`), `--json` (payload to stdout, or to `--out`).

Exit codes: `0` whenever the artifact was written, whatever the verdict says. `1` when there is nothing to export (no run, or no such run id) or the file could not be written. Gating CI on deployment health is `check`'s job; exiting non-zero here on a FAIL would throw away the report that explains it.
