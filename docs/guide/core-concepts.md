---
title: Core Concepts
description: The framework-neutral model at the heart of VoiceGateway: two seams (attach observes, guard controls), RequestRecord and Sink, projects, cost via voice-prices, and ContextVars for session and tenant correlation.
---

# Core Concepts

VoiceGateway sits beside your framework, not between you and it. You keep native LiveKit or Pipecat providers; VoiceGateway adds two thin seams that observe and control those providers. This page explains the model behind those seams and the shared vocabulary the rest of the docs use.

## The two seams

VoiceGateway exposes exactly two public entry points for wiring into an agent:

| Seam | Role | Effect on calls |
|---|---|---|
| `attach(session)` | observe (passive) | none; measures only |
| `guard(provider)` | control (active) | reroutes, throttles, or blocks |

The rule is hard: **observability is `attach`, control is `guard`**. The two seams never call each other. They coordinate through the shared core (spend state, routing ContextVars). Because `attach` is the sole meter, pairing it with `guard` never double-counts.

## attach()

`attach()` takes a LiveKit `AgentSession` or a Pipecat `PipelineTask`, subscribes to its per-component metric events, and writes one `RequestRecord` row per STT, LLM, and TTS call. It returns a session id that ties every row from that conversation together.

```python
from voicegateway import attach

session_id = attach(session, project="my-agent", tenant_id=ctx.room.name)
```

The full signature is documented in [attach()](/guide/attach). The key points here:

- `attach()` is the **single meter**. There is no other path that writes cost or latency rows.
- It is framework-neutral: the same function detects the target type and installs the right observer.
- It is passive: it cannot reroute, throttle, or block.

## guard()

`guard()` wraps a native provider and returns a drop-in replacement of the same type. It adds three controls around the underlying call: fallback chains, rate limiting, and spend caps.

```python
from voicegateway import guard

llm = guard(
    openai.LLM(model="gpt-4o-mini"),
    fallback=[openai.LLM(model="gpt-4o")],
    rate_limit="60/min",
    budget="$5.00/day",
)
```

`guard()` writes **no** metrics. Budget enforcement reads the accumulated spend that `attach()` has already written, closing the measure-then-enforce loop. The full signature and DSL are in [guard()](/guide/guard).

## RequestRecord and Sink

Every row written by `attach()` is a `RequestRecord`: a flat dataclass carrying modality, provider, model, usage units (tokens, characters, audio seconds), priced cost, latency, and the correlation fields (session id, project, agent id, tenant id, channel).

A `Sink` is the destination for those records. Two sinks ship with VoiceGateway:

- **LocalSQLiteSink** (default): writes to a SQLite file on disk, readable by the dashboard and CLI.
- **RemoteCollectorSink**: POSTs records to a hosted ingest endpoint when you set `VOICEGW_COLLECTOR_URL` and `VOICEGW_API_KEY`. Use this for fleet deployments where each agent pushes metrics to a central store.

You can pass a custom `sink=` to `attach()` for testing or alternative backends. The architecture details are in [Storage](/architecture/storage).

## Projects

A project is a named cost and budget scope. Every `RequestRecord` carries a project label. You assign a project at attach time:

```python
attach(session, project="customer-support")
```

Projects are created automatically if they do not already exist. You can pre-configure a project with a daily budget in `voicegw.yaml`:

```yaml
projects:
  customer-support:
    daily_budget: "$20.00"
    budget_action: warn   # warn | throttle | block
```

The budget action is enforced by `guard()` when it checks accumulated spend, not by `attach()`. See [Projects](/configuration/projects) for the full options.

## Cost via voice-prices

VoiceGateway prices every request through `voice-prices`, a fork of `pydantic/genai-prices` extended to cover STT audio minutes and TTS characters in addition to LLM tokens. The cost field in `RequestRecord` is computed from the usage units the provider metric reports:

- **LLM**: prompt tokens, completion tokens, cached tokens priced per provider rate table.
- **TTS**: character count priced per provider per-character rate.
- **STT**: audio duration in seconds priced per provider per-minute rate.

Price tables ship with the library and can be refreshed without a code change. See [Cost Tracking](/architecture/cost-tracking) for the lookup path.

## ContextVars for session and tenant correlation

VoiceGateway uses Python `contextvars.ContextVar` to carry the active session id and tenant id across async tasks without passing them explicitly. When `attach()` runs, it sets the session id in the current context. Every `RequestRecord` written in that context inherits it automatically.

This matters in two situations:

1. **Multi-tenant agents.** Pass `tenant_id=` to `attach()` when one agent instance serves multiple end-users. Each call's record is labelled with the tenant so the dashboard and API can slice costs per customer.
2. **Sub-task correlation.** If your agent spawns helper tasks inside the same async context, their provider calls are automatically tagged with the same session id.

See [Multi-tenant quickstart](/guide/multi-tenant-quickstart) for a worked example.

## What this page is not

This page covers the framework-neutral core. For wiring, go to the seam pages. For limits and budget DSL, go to `guard()`. For config, go to the Configure section.

<CardGroup cols={2}>
  <Card title="attach() (observability)" icon="eye" href="/guide/attach">
    Full signature, options, LiveKit and Pipecat wiring examples, and what each row records.
  </Card>
  <Card title="guard() (control)" icon="shield" href="/guide/guard">
    Fallback chains, rate limiting, and spend caps with LiveKit and Pipecat examples.
  </Card>
  <Card title="Migrate to attach() + guard()" icon="arrow-right" href="/guide/migration-attach-guard">
    Move off the deprecated factories to native providers plus the two seams.
  </Card>
  <Card title="Architecture overview" icon="building" href="/architecture/index">
    How the seams, core, and sink layer fit together end-to-end.
  </Card>
</CardGroup>
