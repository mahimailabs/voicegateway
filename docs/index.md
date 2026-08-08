---
title: VoiceGateway
description: The open-source profiler for voice agents. Meter per-call cost and latency on LiveKit and Pipecat, then profile the SFU and SIP path underneath.
---

VoiceGateway profiles voice agents and the infrastructure they run on. Attach one line to
an agent you already run and per-modality spend lands in the dashboard. Point the same
tool at your LiveKit deployment for SFU health and node capacity, and hand it a load
generator's output to judge the telephony path.

The core is framework-neutral: `import voicegateway` pulls neither LiveKit nor Pipecat.
[`attach()`](/guide/attach) observes, passively, and is the only meter.
[`guard()`](/guide/guard) controls: fallback, rate limits, budgets.

```python
from voicegateway import attach

attach(session, project="my-agent")   # LiveKit AgentSession or Pipecat PipelineTask
```

Start with the [quickstart](/get-started) if you have an agent running, or
[what is VoiceGateway](/guide/what-is-voicegateway) if you want the problem statement
first.
