---
title: What you need
description: The agent, SFU, and SIP layers have different prerequisites. This page sets out all three side by side so you know what must already exist before you install anything.
---

VoiceGateway profiles three layers, and they do not share a setup. The agent layer needs
a pip install and provider keys. The SFU layer needs a LiveKit deployment you already
operate. The SIP layer needs a load generator that VoiceGateway does not ship. Check the
row you care about before you start.

| | Agent | SFU | SIP |
|---|---|---|---|
| Install | `voicegateway[livekit]` or `[pipecat]` | `voicegateway[livekit]` | `voicegateway[livekit]` |
| Extras | `[dashboard]` for `voicegw serve` | `[dashboard]` for `--coordinator` only | `[dashboard]` for node correlation |
| Credentials | your provider API keys | LiveKit URL, key, secret | none for the import itself |
| Must already run | your own agent process | the LiveKit deployment under test | your own SIP load generator |
| Costs real money | yes, your providers bill normally | `voicegw livekit latency` and `check` place real agent turns | whatever your generator drives |

Python 3.11 or later throughout.

<Warning>
Every `voicegw` subcommand currently needs the `livekit` extra, including subcommands
that have nothing to do with LiveKit. The CLI imports its LiveKit diagnostics module at
startup, so a bare `pip install voicegateway` leaves even `voicegw --help` failing with
`ModuleNotFoundError: No module named 'livekit'`. Install `voicegateway[livekit]` even
if you only intend to run `voicegw loadtest`.
</Warning>

## Agent layer

The only layer that needs no infrastructure you do not already have.

```bash
pip install "voicegateway[livekit,dashboard]"
pip install livekit-plugins-openai livekit-plugins-deepgram livekit-plugins-cartesia
```

VoiceGateway bundles no provider wheels. You install the plugins your agent uses and
VoiceGateway prices whatever `model_id` those native instances report. The full extras
table is in [Installation](/guide/installation).

Environment:

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=your-livekit-key
export LIVEKIT_API_SECRET=your-livekit-secret

export DEEPGRAM_API_KEY=...
export OPENAI_API_KEY=...
export CARTESIA_API_KEY=...
```

<Warning>
The three `LIVEKIT_*` variables are not optional for LiveKit Agents. The worker connects
to LiveKit before it runs your entrypoint, so without them the process exits at startup
and nothing reaches VoiceGateway. Pipecat needs none of them unless you use a LiveKit
transport.
</Warning>

Provider keys are read implicitly by the plugin classes. `deepgram.STT()` looks for
`DEEPGRAM_API_KEY` on its own; you do not pass it.

Go to [Quickstart](/get-started).

## SFU layer

This layer profiles a LiveKit media server. VoiceGateway does not provision one. You
need a deployment you operate or administer, self-hosted or LiveKit Cloud, plus
credentials for it.

`voicegateway[livekit]` covers `voicegw livekit agents`, `latency`, `sfu`, `check`, and
`report`. Only distributed coordinator mode needs a second extra:

```bash
pip install "voicegateway[livekit]"
pip install "voicegateway[dashboard]"   # only for voicegw livekit sfu --coordinator
```

Credentials resolve in a fixed order:

1. CLI flags: `--url`, `--api-key`, `--api-secret`
2. Environment: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
3. A `livekit:` block in `voicegw.yaml`

If none resolve, the command exits before making any network call.

<Warning>
`voicegw livekit latency` places real agent turns. The probed agent's STT, LLM, and TTS
providers are invoked with live credentials and bill you normally. Use a low `--trials`
value and scope `--agent` unless you are deliberately benchmarking. `voicegw livekit
check` runs the same probe internally, at two trials per agent, and prints no cost
warning of its own.
</Warning>

The per-component latency breakdown (turn detection, STT, LLM, TTS) additionally
requires the probed agent to be instrumented with [`attach()`](/guide/attach) and writing
to the same store the CLI reads. Without that you get end-to-end latency only.

Go to [Distributed SFU](/deployment/distributed-sfu).

## SIP layer

The layer with the most important caveat, so it goes first:

> VoiceGateway does not place calls and will not become a load generator. It is the
> evidence and reporting layer: something else drives the load, and this reads what it
> left behind.

You supply the load generator. VoiceGateway vendors none of it, and the SIPp-family
tools people use here carry licences that keep them out of this repository. You run it
against your own trunk, then hand VoiceGateway the artifacts:

```bash
voicegw loadtest import ./run-artifacts   # ingest and correlate
voicegw loadtest runs                     # list runs, newest first
voicegw loadtest report <run_id>          # judge the gates, write the evidence bundle
```

`report` takes the run id as a required argument; `runs` is how you find it. No LiveKit
credentials are needed for any of the three.

<Note>
Every import is stamped synthetic unless you pass `--captured`. A forgotten flag produces
a report that labels itself as not-a-deliverable rather than one that quietly claims to
be measured.
</Note>

The report is far more useful with node correlation, which pairs the ramp against CPU,
memory, and file-descriptor headroom on your own fleet. That requires two more things:

```bash
export VOICEGW_NODE_SCRAPE_TARGETS="livekit-server:sfu-1=http://10.0.0.4:6789/metrics,livekit-sip:sip-1=http://10.0.0.5:6789/metrics"
```

and a running `voicegw serve` during the test, because the scrape worker lives in the
server process, not in the CLI. It is read once at startup, so exporting it after the
server is already up does nothing until you restart. With the variable unset the import
still succeeds and every window is reported as `no_samples`.

Go to [Load test evidence](/cli/loadtest).

## Related

- [Which layer do you need?](/guide/decision-tree): pick a layer, then a deployment mode.
- [What you can profile](/guide/what-you-can-profile): what each layer actually measures.
- [Installation](/guide/installation): extras table, Docker, source builds, upgrading.
