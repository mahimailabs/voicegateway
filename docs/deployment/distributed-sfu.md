---
title: "Distributed SFU probers"
description: "Load-test a LiveKit SFU concurrently from several regions using a coordinator and per-region prober containers."
---
`voicegw livekit sfu --load` measures SFU capacity from one host. Distributed mode asks a harder question: does the SFU hold up when clients from several regions join the same room at once. A coordinator synchronizes N probers, one per region, ramping that room together.

See [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu) for the combined-report format; this page covers deploying probers.

## How it works

1. The coordinator (`sfu --coordinator --expect N`) hands every prober the same job (room, ramp tiers, duration, thresholds) over a small HTTP barrier, and a shared `start_at` once all N have registered.
2. Each prober (`sfu --report-to <url> --vantage <label>`) registers, waits for the barrier, ramps the shared room, and posts its per-tier measurements back.
3. Once every prober has reported, the coordinator aggregates (sum clients per tier, worst rtt / loss / quality), prints the combined capacity, and deletes the shared rooms.

Coordinator: `[dashboard,livekit]` extras (`pip install 'voicegateway[dashboard,livekit]'`). Probers: `[livekit]`, not the base install. The CLI imports the LiveKit diagnostics modules at startup, so even `voicegw --help` fails with `ModuleNotFoundError` without it.

## Coordinator

Run the coordinator somewhere probers can reach over HTTP:

```bash
pip install 'voicegateway[dashboard,livekit]'
export LIVEKIT_URL=wss://your.livekit.cloud
export LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...

voicegw livekit sfu --coordinator --expect 3 \
    --ramp 10,25,50 --duration 20s --coordinator-port 8787
```

It blocks until all three probers report, then prints the combined report and exits.

## Watching the run in Grafana

`deploy/grafana/voicegateway-load-test.json` is an importable Grafana dashboard reading from the same `node_samples` table a run writes to, so ramp metrics land on the same panels as everything else. See [Node metrics](/guide/node-metrics) for the scrape configuration, data sources, and panel inventory.

## Placing load with the mock participant

`tools/mock-participant/` is a Go module that joins a room as a LiveKit agent worker and reports per-call observations to the collector: it puts calls on the SFU while the probers measure it.

No root `go.mod` covers it, so build it from its own directory:

```bash
(cd tools/mock-participant && go build .)
```

## Probers on Fly.io

`deploy/prober/` ships a `Dockerfile` and an example `fly.toml`: a run-to-completion image, one prober per machine.

The shipped `Dockerfile` has no extras in its `pip install`, so the entrypoint crashes on start. Change that line to install `voicegateway[livekit]` before you build.

```bash
cd deploy/prober
fly apps create vg-sfu-prober
fly secrets set -a vg-sfu-prober \
    LIVEKIT_URL=wss://your.livekit.cloud \
    LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \
    COORDINATOR_URL=http://<coordinator-host>:8787
fly deploy -a vg-sfu-prober
fly scale count 3 --region iad,sjc,lhr -a vg-sfu-prober
```

Each machine reads its vantage label from `VOICEGW_REGION`, which the entrypoint passes to `--vantage`. Fly injects `FLY_REGION`, not `VOICEGW_REGION`, so map one to the other in `fly.toml` or every prober reports an empty vantage. Ramp and duration come from the coordinator's job. Any container host works the same way, given the same env vars and a `[livekit]`-built image.

## Limitations

- Per-tier concurrency drifts after the first tier: the barrier synchronizes only the shared start, and each vantage advances to its next tier as soon as its own measurement finishes. Combined per-tier sums are exact at tier one, an upper bound after. Lean on baseline and first-tier numbers for the tightest signal.
- If a prober dies, the run degrades instead of hanging. The coordinator stops after its timeout (default 10 minutes) and aggregates whatever reported; a missing vantage shows up under `dropped` in the report.

## Cost and safety

- The coordinator listens on port 8787 by default with no authentication on `/register`, `/report/{prover_id}`, or `/result`: anyone who can reach the port can inject fake reports or read the result. Run it on a private network (a VPC, Fly private networking, an SSH tunnel), not a public interface, only for the run's duration.
- Distributed probing opens real SFU connections from many hosts at once. Unlike `latency`, it does not invoke STT/LLM/TTS providers, so there is no per-turn cost. It does consume SFU capacity for the ramp's duration: use a test project or a maintenance window, not production traffic.

## Related

- [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu): the command reference and combined-report format.
- [Node metrics](/guide/node-metrics): the Grafana dashboard this page's runs feed.
- [SIP load testing](/cli/loadtest): correlate a call-volume ramp against these same node metrics.
- [Deploy on Fly.io](/deployment/fly): deploying the VoiceGateway daemon itself.
