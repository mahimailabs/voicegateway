---
title: What you can profile
description: "VoiceGateway measures three layers: the agent's inference cost and latency, the SFU carrying the media, and the SIP path that answered the phone. They need different things."
---

A voice call crosses more than one system. The caller arrives over SIP, the media lands on
an SFU, an agent is dispatched into the room, and that agent calls three inference
providers. When a call is slow or expensive, the answer can be in any of them.

## The three layers

| | Agent | SFU | SIP |
|---|---|---|---|
| Answers | what the call cost, where latency went | is the media server healthy, what is its capacity | did the telephony path answer, how fast |
| Measures | STT audio-minutes, LLM tokens, TTS characters, per-request latency | connection quality, RTT, a concurrency ramp, per-node CPU and memory | answer latency, failure classification, the capacity knee |
| Entry point | [`attach()`](/guide/attach) | [`voicegw livekit`](/cli/livekit) | [`voicegw loadtest`](/cli/loadtest) |
| Install | `voicegateway[livekit]` or `[pipecat]` | `voicegateway[livekit]` | `voicegateway[livekit]` |
| Credentials | your provider API keys | LiveKit URL, key, secret | none |
| Must already run | your own agent process | the LiveKit deployment under test | your own SIP load generator |
| Costs real money | yes, your providers bill normally | `latency` and `check` place real agent turns | whatever your generator drives |

Python 3.11 or later throughout.

<Warning>
Every `voicegw` subcommand needs the `livekit` extra, including ones with nothing to do
with LiveKit. The CLI imports its LiveKit diagnostics at startup, so a bare
`pip install voicegateway` leaves even `voicegw --help` failing with
`ModuleNotFoundError`. Install `voicegateway[livekit]` even if you only run
`voicegw loadtest`.
</Warning>

The agent layer stands alone. You can profile cost and latency without operating any
infrastructure beyond your own agent process, and most people stay there. The SFU and SIP
layers assume you already run the deployment under test, which is usually a different job
and often a different person.

## The SIP layer works differently

> VoiceGateway does not place calls and will not become a load generator. It is the
> evidence and reporting layer: something else drives the load, and this reads what it
> left behind.

You run the generator against your own trunk, then hand VoiceGateway the artifacts.
`voicegw loadtest import` ingests and correlates them; `voicegw loadtest report <run_id>`
judges the gates and writes the evidence bundle.

## How the layers join

Each layer is written by its own path: LiveKit webhooks fill `calls` and `call_legs`, the
node scrape fills `node_samples`, and `attach()` writes sessions and requests. No single
row joins all three. The joins are pairwise and deliberately conservative:

| Join | Key | When unsure |
|---|---|---|
| Session to call | LiveKit room name | ambiguous names are refused, not guessed |
| Node samples to call | timestamp overlap | reported as `correlated`, `no_samples`, or `scrape_failed` |

`GET /api/correlation` reports the deployment-wide join rate so you can see how complete
the picture is instead of assuming it.

### The per-call waterfall

The dashboard renders six layers per call. Two carry a number.

| Layer | Measured | Why not |
|---|---|---|
| SIP | no | `livekit-api` exposes no per-call SIP response code or RTP detail |
| SFU | no | the SFU reports no per-call transport timing |
| Dispatch | **yes** | caller in the room to the agent joining |
| Agent | **yes** | agent joining to its first published audio |
| Inference | no | `calls` and `call_legs` carry no STT, LLM, or TTS timing |
| Provider | no | same reason |

The unmeasured rows render their reason rather than an empty bar. Per-modality inference
timing does exist, from `attach()`, on the cost and latency views. It is not joined into
this waterfall.

## What it does not do

- It does not generate SIP load. Not one call.
- It does generate SFU traffic when you ask: `voicegw livekit latency` places real agent turns, `voicegw livekit sfu --load` connects synthetic participants. Both are opt-in.
- It does not provision infrastructure.
- It does not claim a causal join. Node overlap means a sample fell inside a call's window, not that the node served that call.
