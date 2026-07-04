---
title: Distributed SFU probers
description: Load-test a LiveKit SFU concurrently from several regions using a coordinator and per-region prober containers.
---

# Distributed SFU probers

`voicegw livekit sfu --load` measures SFU capacity from a single host. That answers "what can this one machine push," not "does my SFU hold up when clients from several regions join the same room at once." Distributed mode answers the second question: one **coordinator** synchronizes N **probers**, each running from a different region, all ramping the same room together.

See the [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu) reference for the combined-report format. This page covers deploying the probers.

## How it works

1. The **coordinator** (`sfu --coordinator --expect N`) serves a small HTTP barrier. It hands every prover the same job (room, ramp tiers, duration, thresholds) and a shared `start_at` timestamp once all N have registered.
2. Each **prober** (`sfu --report-to <url> --vantage <label>`) registers, waits for the barrier so all vantages start at the same instant, ramps the shared room, and posts its per-tier measurements back.
3. When every prover has reported, the coordinator aggregates (summing clients per tier, taking the worst rtt / loss / quality), prints the combined capacity, and deletes the shared rooms.

The coordinator needs the `[server]` extra (`pip install 'voicegateway[server]'`) for its HTTP layer. Probers need only the base install.

## Coordinator

Run the coordinator somewhere the probers can reach over HTTP (a bastion host, a small VM, or a Fly machine with an internal address):

```bash
pip install 'voicegateway[server]'
export LIVEKIT_URL=wss://your.livekit.cloud
export LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...

voicegw livekit sfu --coordinator --expect 3 \
    --ramp 10,25,50 --duration 20s --coordinator-port 8787
```

It blocks until all three probers report, then prints the combined report and exits.

## Probers on Fly.io

The `deploy/prober/` directory ships a `Dockerfile` and an example `fly.toml`. The image is a run-to-completion job that runs one prober and exits.

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

Each machine reads its region from Fly's `FLY_REGION` and reports it as its vantage label, so a machine in `sjc` shows up as the `sjc` vantage with no per-region config. `RAMP` and `DURATION` are set in `fly.toml` and must match what you pass the coordinator.

Any host that can run a container works the same way: set `COORDINATOR_URL`, the LiveKit creds, and `VOICEGW_REGION`, then run the image. Fly is just a convenient way to place probers in specific regions.

## Cost and safety

Distributed probing opens real SFU connections from many hosts at once. Unlike `latency`, it does not invoke STT/LLM/TTS providers (there is no agent in the loop), so there is no per-turn provider cost. It does consume SFU capacity for the duration of the ramp, so run it against a test project or during a maintenance window, not against production traffic.

## Related

- [`voicegw livekit sfu`](/cli/livekit#voicegw-livekit-sfu): the command reference and combined-report format.
- [Deploy on Fly.io](/deployment/fly): deploying the VoiceGateway engine itself.
