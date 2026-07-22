---
title: "Distributed SFU probers"
description: "Load-test a LiveKit SFU concurrently from several regions using a coordinator and per-region prober containers."
---

# Distributed SFU probers

`voicegw livekit sfu --load` measures SFU capacity from a single host. That answers "what can this one machine push," not "does my SFU hold up when clients from several regions join the same room at once." Distributed mode answers the second question: one coordinator synchronizes N probers, each running from a different region, all ramping the same room together.

See the [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu) reference for the combined-report format. This page covers deploying the probers.

## How it works

1. The coordinator (`sfu --coordinator --expect N`) serves a small HTTP barrier. It hands every prober the same job (room, ramp tiers, duration, thresholds) and a shared `start_at` timestamp once all N have registered.
2. Each prober (`sfu --report-to <url> --vantage <label>`) registers, waits for the barrier so all vantages start at the same instant, ramps the shared room, and posts its per-tier measurements back.
3. When every prober has reported, the coordinator aggregates (summing clients per tier, taking the worst rtt / loss / quality), prints the combined capacity, and deletes the shared rooms.

The coordinator needs the `[dashboard]` extra (`pip install 'voicegateway[dashboard]'`) for its HTTP layer. Probers need only the base install.

## Coordinator

Run the coordinator somewhere the probers can reach over HTTP (a bastion host, a small VM, or a Fly machine with an internal address):

```bash
pip install 'voicegateway[dashboard]'
export LIVEKIT_URL=wss://your.livekit.cloud
export LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...

voicegw livekit sfu --coordinator --expect 3 \
    --ramp 10,25,50 --duration 20s --coordinator-port 8787
```

It blocks until all three probers report, then prints the combined report and exits.

## Probers on Fly.io

The `deploy/prober/` directory ships a `Dockerfile` and an example `fly.toml`. The image is a run-to-completion job that runs one prober and exits.

<Steps>

### Create the Fly app

```bash
cd deploy/prober
fly apps create vg-sfu-prober
```

### Set secrets

```bash
fly secrets set -a vg-sfu-prober \
    LIVEKIT_URL=wss://your.livekit.cloud \
    LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \
    COORDINATOR_URL=http://<coordinator-host>:8787
```

### Deploy and scale across regions

```bash
fly deploy -a vg-sfu-prober
fly scale count 3 --region iad,sjc,lhr -a vg-sfu-prober
```

</Steps>

Each machine reads its region from Fly's `FLY_REGION` and reports it as its vantage label, so a machine in `sjc` shows up as the `sjc` vantage with no per-region config. The ramp and duration are dictated by the coordinator (every vantage runs the same job), so there is nothing to set for them on the prober.

Any host that can run a container works the same way: set `COORDINATOR_URL`, the LiveKit creds, and `VOICEGW_REGION`, then run the image. Fly is just a convenient way to place probers in specific regions.

## Limitations

- The coordinator endpoint is unauthenticated. `/register`, `/report`, and `/result` have no auth, so anyone who can reach the port can inject fake reports or read the result. Run the coordinator on a private network the probers can reach (a VPC, Fly private networking, an SSH tunnel), not a public interface, and only for the duration of the run.
- Per-tier concurrency drifts after the first tier. The barrier synchronizes only the shared start; each vantage then advances to its next ramp tier as soon as its own measurement finishes. Vantages stay aligned at the first tier, but faster ones run ahead on later tiers, so the combined per-tier client sums are exact at tier one and an upper bound thereafter. Read the combined knee as approximate, and lean on the baseline and first-tier numbers for the tightest signal.
- If a prober dies, the run degrades rather than hangs. The coordinator stops after its timeout (default 10 minutes) and aggregates whatever reported; a prober that never clears the barrier gives up after its own timeout. A missing vantage shows up under `dropped` in the report.

## Cost and safety

Distributed probing opens real SFU connections from many hosts at once. Unlike `latency`, it does not invoke STT/LLM/TTS providers (there is no agent in the loop), so there is no per-turn provider cost. It does consume SFU capacity for the duration of the ramp, so run it against a test project or during a maintenance window, not against production traffic.

## Related

- [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu): the command reference and combined-report format.
- [Deploy on Fly.io](/deployment/fly): deploying the VoiceGateway daemon itself.
