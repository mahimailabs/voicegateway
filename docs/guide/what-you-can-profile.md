---
title: What you can profile
description: "VoiceGateway measures three layers of a voice deployment: the agent's inference cost and latency, the SFU carrying the media, and the SIP path that answered the phone."
---

A voice call crosses more than one system. The caller arrives over SIP, the media lands
on an SFU, an agent is dispatched into the room, and that agent calls three inference
providers. When a call is slow or expensive, the answer can be in any of those layers.

VoiceGateway measures all three, and ties them to the same call where the data allows.

## The agent layer

What one conversation cost, and where its latency went.

[`attach()`](/guide/attach) subscribes to the metric events your framework already emits
and writes one row per provider request: modality, provider, model, usage units, priced
cost, time to first byte, and total latency. It never sits in the audio or inference
path.

- **Measures:** STT audio-minutes, LLM prompt and completion tokens, TTS characters, each priced through `voice-prices`
- **Also captures:** per-request latency, the session id that groups a conversation, and whether the call arrived over telephony or web
- **Needs:** a pip install and your provider keys. No infrastructure you do not already run.

[`guard()`](/guide/guard) is the control counterpart: fallback chains, rate limits, and
spend caps on the providers you choose. It writes no metrics, so pairing it with
`attach()` never double-counts.

## The SFU layer

Whether the media server is healthy, and how much it will hold.

`voicegw livekit` runs five subcommands against a LiveKit deployment you operate:
`agents` lists the worker fleet, `latency` probes end-to-end response time, `sfu`
measures connection quality and ramps concurrency, `check` runs the gates together, and
`report` exports a run that already happened as one self-contained HTML file.

- **Measures:** connection quality and round-trip time, a concurrency ramp to find the capacity knee, and per-node CPU, memory, file descriptors, and Go heap when the Prometheus scrape is configured
- **Also captures:** live rooms, egress jobs, and ingress endpoints on the dashboard's Server page
- **Needs:** LiveKit credentials for a deployment you already operate. VoiceGateway does not provision one.

`voicegw livekit latency` places real agent turns and bills your providers normally. See
[What you need](/guide/prerequisites) before running it.

## The SIP layer

Whether the telephony path answered, and how fast.

This layer works differently from the other two, and the difference matters:

> VoiceGateway does not place calls and will not become a load generator. It is the
> evidence and reporting layer: something else drives the load, and this reads what it
> left behind.

You run a SIP load generator against your own trunk. `voicegw loadtest import` reads its
artifacts and correlates them against node metrics by time window; `voicegw loadtest
report <run_id>` judges the gates and writes the evidence bundle that says where capacity
broke.

- **Measures:** answer latency (including true INVITE-to-200-OK wall time when the generator reports it), failure classification, and the capacity knee against node headroom
- **Also captures:** inbound and outbound trunks and dispatch rules, read-only, on the dashboard's Server page
- **Needs:** a load generator you supply. VoiceGateway ships none.

## How the layers join

Each layer is written by its own path. LiveKit webhooks write room, participant, and
track lifecycle into `calls` and `call_legs`, where every leg carries a kind (`SIP`,
`AGENT`, `INGRESS`, `EGRESS`, `STANDARD`). The node scrape writes `node_samples`.
`attach()` writes sessions and requests. Agents and load workers can self-report legs
through `POST /v1/calls/observations`.

There is no single row that joins all three. The joins are pairwise, computed after the
fact, and each is deliberately conservative:

| Join | Key | Behavior when unsure |
|---|---|---|
| Session to call | LiveKit room name | An ambiguous room name is refused, not guessed. The session stays uncorrelated |
| Node samples to call | timestamp overlap with the call's span | Reported as `correlated`, `no_samples`, or `scrape_failed` per call |

`GET /api/correlation` reports the deployment-wide join rate, with the ambiguous and
dangling cases broken out, so you can see how complete the picture is instead of
assuming it.

### The per-call waterfall, and what it cannot show

The dashboard renders a six-layer waterfall per call: SIP, SFU, Dispatch, Agent,
Inference, Provider. Two of those carry a number today.

| Layer | Measured | Why |
|---|---|---|
| SIP | no | `livekit-api` exposes no per-call SIP response code or RTP detail |
| SFU | no | the SFU reports no per-call transport timing. The SFU baseline elsewhere is a fleet round trip, not this call |
| Dispatch | **yes** | caller in the room to the agent joining |
| Agent | **yes** | agent joining to its first published audio |
| Inference | no | `calls` and `call_legs` carry no STT, LLM, or TTS timing, so the model's share of the agent layer is unknown here |
| Provider | no | same reason |

The unmeasured rows render the reason rather than an empty bar or a zero. Per-modality
inference timing does exist, from `attach()`, on the cost and latency views. It is simply
not joined into this waterfall.

Answer-latency precision also depends on who reported it. The webhook-only path truncates
to whole seconds; millisecond precision requires a self-reporting agent or load worker.

## What this does not do

- It does not generate SIP load. Not one call. That is the load generator's job, and VoiceGateway ships none.
- It does generate some traffic on the SFU layer, deliberately: `voicegw livekit latency` places real agent turns, and `voicegw livekit sfu --load` connects synthetic WebRTC participants in a concurrency ramp. Both are probes against a deployment you own, and both are opt-in.
- It does not provision infrastructure. The SFU and SIP layers profile a deployment you already run.
- It does not proxy inference. There is no request hop and no added latency on happy-path calls.
- It does not claim a causal join. Node overlap means a sample fell inside a call's window, not that the node served that call.
- It does not require all three layers. Metering the agent is a complete use of the tool.

## Where to go next

<CardGroup cols={2}>
  <Card title="What you need" icon="list-check" href="/guide/prerequisites">
    Prerequisites for all three layers, side by side.
  </Card>
  <Card title="Quickstart" icon="bolt" href="/get-started">
    Agent layer: install, attach, first cost row.
  </Card>
  <Card title="voicegw livekit" icon="server" href="/cli/livekit">
    SFU layer: agents, latency, sfu, check, report.
  </Card>
  <Card title="voicegw loadtest" icon="phone" href="/cli/loadtest">
    SIP layer: import a run, read the capacity verdict.
  </Card>
</CardGroup>
