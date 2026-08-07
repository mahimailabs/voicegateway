---
title: VoiceGateway
description: The open-source profiler for voice agents. Meter per-call cost and latency on LiveKit and Pipecat, then profile the SFU and SIP path underneath, from one dashboard.
---

VoiceGateway profiles voice agents and the infrastructure they run on. Attach one line
to an agent you already run and per-modality spend lands in the dashboard. Point the
same tool at your LiveKit deployment for SFU health and node capacity, and hand it a
load generator's output to judge the telephony path.

The core is framework-neutral: `import voicegateway` pulls neither LiveKit nor
Pipecat. Two seams do the work. [`attach()`](/guide/attach) observes (passive: cost
and latency). [`guard()`](/guide/guard) controls (active: fallback, rate limits,
budgets).

```python
from voicegateway import attach

attach(session, project="my-agent")   # LiveKit AgentSession or Pipecat PipelineTask
```

## Three layers, one call

| Layer | What it answers | Needs |
|---|---|---|
| [Agent](/guide/attach) | What did this conversation cost, and where did the latency go? | A pip install and your provider keys |
| [SFU](/cli/livekit) | Is the media server healthy, and how many calls will it hold? | A LiveKit deployment you operate |
| [SIP](/cli/loadtest) | Did the telephony path answer, and how fast? | A load generator you run yourself |

The layers have genuinely different prerequisites. [What you need](/guide/prerequisites)
sets out all three before you install anything.

## Start here

<CardGroup cols={2}>
  <Card title="Quickstart" icon="bolt" href="/get-started">
    Install, attach to your agent, and read your first cost row.
  </Card>
  <Card title="What is VoiceGateway" icon="circle-question" href="/guide/what-is-voicegateway">
    The problem it solves and the two-seam model behind it.
  </Card>
  <Card title="What you can profile" icon="layer-group" href="/guide/what-you-can-profile">
    Agent, SFU, and SIP: what each layer measures and what it cannot.
  </Card>
  <Card title="Which layer do you need?" icon="signs-post" href="/guide/decision-tree">
    Route by layer, then by self-host or Cloud.
  </Card>
  <Card title="Hosted Cloud" icon="cloud" href="/hosted/quickstart">
    Skip the local daemon. Point your agent at the hosted collector.
  </Card>
  <Card title="CLI reference" icon="terminal" href="/cli/index">
    The `voicegw` commands, one page each: serve, costs, livekit, loadtest, reconcile, and the rest.
  </Card>
</CardGroup>
