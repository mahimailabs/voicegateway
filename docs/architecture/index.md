---
title: Architecture Overview
description: "The two instrumentation seams, attach() and guard(), and how the source tree is organized: core, inference, middleware, models, repository, services, and server."
---

VoiceGateway does not sit in the inference path. You construct your own LiveKit plugin instances or Pipecat services the way you always have; two functions instrument them from the outside:

- **`attach(session)`**: a passive observer. It reads STT/LLM/TTS metrics off the session's own event stream, prices them via `voice-prices`, and writes a `RequestRecord`.
- **`guard(provider)`**: an active wrapper around one already-constructed provider instance. It adds fallback, a rate limit, and a spend cap in front of that provider, and writes no metrics of its own.

The two compose without double-counting: `guard()` never meters, `attach()` is the only writer. See [attach()](/guide/attach) and [guard()](/guide/guard) for full signatures and wiring examples.

## Request flow

```mermaid
graph TD
    subgraph Code["Your agent code"]
        P["Native plugin instance<br/>(deepgram.STT, openai.LLM, ...)"]
    end

    subgraph Observe["attach() -- passive observer"]
        MC["MetricCapture<br/>subscribes to metrics_collected"]
        CT["CostTracker"]
        PRC["voice-prices catalog"]
        REC["RequestRecord"]
    end

    subgraph Control["guard() -- optional active wrapper"]
        RL["rate_limit token bucket"]
        BUD["budget spend-cap check"]
        FB["fallback walk"]
    end

    subgraph Sink["Sink"]
        DB[(local SQLite)]
        COL["remote collector"]
    end

    P -->|attach binds to the session| MC
    MC --> CT
    CT --> PRC
    CT --> REC
    REC --> DB
    REC --> COL
    DB --> UI["Dashboard / CLI / HTTP API"]

    P -.->|optionally wrapped by| FB
    FB --> RL
    FB --> BUD
    FB -.->|on primary failure, sets a ContextVar<br/>attach() reads: fallback_from + status| REC
```

`attach()` subscribes directly to the `metrics_collected` events LiveKit and Pipecat already emit per component; it does not wrap or replace the plugin instance. `guard()` does wrap: it returns a same-type drop-in that runs its checks before delegating to the real provider (or a fallback). Neither reads from nor writes through the `BaseProvider` classes described in [Provider Abstraction](/architecture/provider-abstraction).

## Directory layout

```
src/voicegateway/
├── core/                    # Gateway singleton, config load/merge, provider registry
│   ├── gateway.py           # Gateway: wires config + storage + middleware together
│   ├── gateway_factory.py   # process-wide Gateway singleton (get_gateway())
│   ├── config.py            # GatewayConfig.load(): YAML + ${ENV_VAR} + Pydantic validation
│   ├── config_manager.py    # merges YAML with managed_* SQLite rows
│   ├── registry.py          # provider name -> class map, used for health checks only
│   └── crypto.py            # Fernet encryption for provider keys stored in SQLite
├── inference/
│   ├── providers/           # BaseProvider ABC + 11 provider classes (health-check only)
│   ├── pricing/             # voice-prices facade (catalog.py)
│   ├── livekit/             # guard() LiveKit implementation
│   ├── pipecat/             # attach() observer + guard() Pipecat implementation
│   └── session/             # attach(), MetricCapture, session ContextVars
├── middleware/               # cost_tracker_middleware.py, rate_limiter_middleware.py,
│                              # budget_enforcer_middleware.py, instrumented_provider_middleware.py, ...
├── models/                   # dataclasses: RequestRecord, session, worker, replay event, ...
├── repository/                # one file per table: cost, session, replay, workers, ...
├── services/                  # StorageService, sinks (local SQLite / collector / ClickHouse), billing, retention
└── server/
    ├── api/                   # FastAPI routes: /v1/costs, /v1/logs, /v1/providers, ...
    └── mcp/                   # MCP server (voicegw mcp)
```

```
src/dashboard/
├── frontend/    # React + TypeScript + Vite dashboard (Recharts, Neo-Brutalism aesthetic)
├── console/     # smaller SPA built on @openorca-ui/react
└── api/         # static/branding/ images only, no routes
```

There is no separate dashboard backend process. The combined server in `src/voicegateway/server/` serves the built frontend SPA at `/` (see `server/static.py`); the legacy standalone dashboard API was removed in 2026-05.

## Design principles

**Async throughout.** Database, HTTP, and provider operations use `async`/`await`. `attach()` and `guard()` are async-native from the caller's perspective (`guard()`'s wrapped methods are coroutines/async streams).

**Framework neutral.** VoiceGateway does not own the inference path or bundle provider wheels. You install the plugin wheels your agent uses (for example `pip install livekit-plugins-openai`); VoiceGateway meters those instances by `model_id` via voice-prices without ever importing the provider SDK itself.

**attach() reads identity off the live instance, not off a config string.** `attach()`/`guard()` build `provider/model` from the plugin's own `.provider`/`.model` attributes (`component_identity()` in `inference/session/capture.py`), the reverse of parsing a string to look something up. See [Models and stacks](/configuration/models) for the exact `provider/model` format this produces.

**guard() wraps, attach() forwards.** `guard()`'s wrappers subclass the matching `livekit.agents` base class directly and override the methods that matter (`chat`, `recognize`, `synthesize`, `stream`); a `__getattr__` fallback only catches provider-specific extras the base class doesn't declare. Every wrapper forwards the inner plugin's `metrics_collected` event transparently, so an `attach()` bound to the same session still sees it once.

**Config layering.** Three sources merge at startup: environment variables (highest priority), SQLite managed tables (dashboard/MCP writes), and YAML (base config). See [Configuration layers](/architecture/config-layers).

**Encryption at rest.** Provider API keys stored in SQLite are encrypted with Fernet (AES-128-CBC + HMAC-SHA256, via `MultiFernet` so a rotated key can still decrypt old rows). Keys in API responses are masked to the `secr...2345` format. See [Security model](/architecture/security).

## Architecture pages

<CardGroup cols={2}>
  <Card title="Gateway Core" href="/architecture/gateway-core">
    The Gateway class, config loading, and the provider registry.
  </Card>
  <Card title="Provider Abstraction" href="/architecture/provider-abstraction">
    BaseProvider ABC and the 11 provider health-check implementations.
  </Card>
  <Card title="Middleware" href="/architecture/middleware">
    Cost tracking, rate limiting, and what attach()/guard() actually wire in.
  </Card>
  <Card title="Cost Tracking" href="/architecture/cost-tracking">
    Per-modality cost calculation via voice-prices.
  </Card>
  <Card title="Rating" href="/architecture/rating">
    Turning recorded cost into a billable price with a rate card.
  </Card>
  <Card title="Configuration Layers" href="/architecture/config-layers">
    YAML + SQLite + env merge and the ConfigManager.
  </Card>
  <Card title="Storage" href="/architecture/storage">
    SQLite schema, views, and the ClickHouse sink.
  </Card>
  <Card title="Security Model" href="/architecture/security">
    Key encryption, secret masking, and MCP auth.
  </Card>
  <Card title="Fleet Worker Heartbeat" href="/architecture/fleet-worker-heartbeat">
    The heartbeat contract between agents and the roster.
  </Card>
  <Card title="Replay Storage Costs" href="/storage/replay-storage-costs">
    On-disk footprint of session replay capture.
  </Card>
</CardGroup>
