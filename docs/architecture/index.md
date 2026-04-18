# Architecture Overview

VoiceGateway is a self-hosted inference gateway for voice AI. It provides unified routing for STT, LLM, and TTS requests across cloud providers and local models, with cost tracking, fallback chains, rate limiting, budget enforcement, and a web dashboard.

## System Architecture

```mermaid
graph TB
    subgraph UserCode["User Code / LiveKit Agent"]
        A["gateway.stt('deepgram/nova-3')"]
        B["gateway.llm('openai/gpt-4.1-mini')"]
        C["gateway.tts('cartesia/sonic-3')"]
    end

    subgraph Core["Core Layer"]
        GW[Gateway]
        R[Router]
        REG[Registry]
        MID[ModelId Parser]
    end

    subgraph Middleware["Middleware Pipeline"]
        BE[BudgetEnforcer]
        IP[InstrumentedProvider]
        CT[CostTracker]
        LM[LatencyMonitor]
        RL[RateLimiter]
        FB[FallbackChain]
        LG[RequestLogger]
    end

    subgraph Providers["Provider Layer"]
        BP[BaseProvider ABC]
        OAI[OpenAI]
        DG[Deepgram]
        CA[Cartesia]
        AN[Anthropic]
        GR[Groq]
        EL[ElevenLabs]
        AA[AssemblyAI]
        OL[Ollama]
        WH[Whisper]
        KO[Kokoro]
        PI[Piper]
    end

    subgraph Storage["Storage Layer"]
        DB[(SQLite)]
        CM[ConfigManager]
        CR[Crypto / Fernet]
    end

    subgraph Interfaces["External Interfaces"]
        API[FastAPI HTTP Server]
        DASH[Dashboard - React/Vite]
        MCP[MCP Server]
        CLI[CLI - voicegw]
    end

    A --> GW
    B --> GW
    C --> GW
    GW --> BE
    BE --> R
    R --> MID
    R --> REG
    REG --> BP
    BP --> OAI & DG & CA & AN & GR & EL & AA & OL & WH & KO & PI
    GW --> IP
    IP --> CT
    IP --> LM
    GW --> FB
    CT --> DB
    API --> GW
    DASH --> API
    MCP --> GW
    CLI --> GW
    CM --> DB
    CR --> DB
```

## Request Flow

Every call to `gateway.stt()`, `gateway.llm()`, or `gateway.tts()` follows the same path:

```mermaid
sequenceDiagram
    participant App as User Code
    participant GW as Gateway
    participant BE as BudgetEnforcer
    participant R as Router
    participant MID as ModelId
    participant REG as Registry
    participant P as Provider
    participant IP as InstrumentedProvider
    participant CT as CostTracker
    participant DB as SQLite

    App->>GW: gateway.stt("deepgram/nova-3", project="prod")
    GW->>BE: check_budget("prod")
    BE->>DB: get_cost_summary("today", project="prod")
    DB-->>BE: $4.20 / $10.00 budget
    BE-->>GW: OK (under budget)
    GW->>R: resolve("deepgram/nova-3", "stt")
    R->>MID: parse("deepgram/nova-3")
    MID-->>R: ModelId(provider="deepgram", model="nova-3")
    R->>REG: create_provider("deepgram", config)
    REG-->>R: DeepgramProvider instance
    R->>P: create_stt(model="nova-3")
    P-->>R: STT instance
    R-->>GW: STT instance
    GW->>IP: wrap_provider(instance, "stt", ...)
    IP-->>GW: InstrumentedSTT wrapper
    GW-->>App: InstrumentedSTT (proxies all access)
    Note over IP,DB: On first byte/completion, records TTFB + latency
    IP->>CT: create_record(...)
    CT->>DB: log_request(record)
```

## Directory Structure

```
voicegateway/
  core/
    gateway.py          # Main Gateway class — orchestrator
    config.py           # YAML config loader with ${ENV_VAR} substitution
    config_manager.py   # Merges YAML + SQLite + env (priority: env > db > yaml)
    router.py           # Resolves "provider/model" to provider instances
    registry.py         # Lazy provider factory (imports on first use)
    model_id.py         # Parses "provider/model[:variant]" format
    schema.py           # Pydantic validation for voicegw.yaml
    crypto.py           # Fernet encryption for stored secrets
  providers/
    base.py             # BaseProvider ABC (create_stt/llm/tts, health_check, get_pricing)
    openai_provider.py  # OpenAI (STT + LLM + TTS)
    deepgram_provider.py
    cartesia_provider.py
    anthropic_provider.py
    groq_provider.py
    elevenlabs_provider.py
    assemblyai_provider.py
    ollama_provider.py
    whisper_provider.py
    kokoro_provider.py
    piper_provider.py
  middleware/
    cost_tracker.py         # Per-request cost calculation and storage
    latency_monitor.py      # TTFB + total latency tracking
    rate_limiter.py         # Token bucket rate limiter per provider
    fallback.py             # Automatic model failover chains
    logger.py               # Structured request/response logging
    budget_enforcer.py      # Project budget enforcement (warn/throttle/block)
    instrumented_provider.py # Transparent proxy wrappers for metrics
  storage/
    sqlite.py           # SQLite backend (aiosqlite)
    models.py           # RequestRecord dataclass
  server.py             # FastAPI HTTP API
  mcp/
    server.py           # MCP server bootstrap
    auth.py             # API key authentication
    errors.py           # Structured error types
    schemas.py          # Input/output schemas
    tools/              # Tool implementations (providers, models, projects, observability)
  pricing/
    catalog.py          # Per-model pricing data
dashboard/
  api/                  # FastAPI backend for dashboard
  frontend/             # React + TypeScript + Vite + Recharts
```

## Design Principles

1. **Async throughout** -- all database, HTTP, and provider operations use async/await. The Gateway provides synchronous wrapper methods for convenience.

2. **Lazy loading** -- providers are only imported and instantiated on first use. `pip install voicegateway[openai]` installs only the OpenAI SDK.

3. **Transparent instrumentation** -- `InstrumentedSTT/LLM/TTS` wrappers proxy all attribute access via `__getattr__`, so user code sees the exact same API as the underlying provider instance.

4. **Config layering** -- three sources merged at startup: environment variables (highest priority), SQLite managed tables (dashboard/MCP writes), and YAML (base config). Each resource carries a `source` field (`"yaml"` or `"db"`).

5. **Encryption at rest** -- all API keys stored in SQLite are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). Keys in API responses are masked to `secr...2345` format.

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| [Gateway Core](./gateway-core) | `core/gateway.py` | Main orchestrator, entry point for all requests |
| [Provider Abstraction](./provider-abstraction) | `providers/base.py` | ABC for all 11 provider implementations |
| [Middleware](./middleware) | `middleware/` | Cost, latency, rate limiting, fallback, budget |
| [Storage](./storage) | `storage/sqlite.py` | SQLite schema, tables, views, indexes |
| [Config Layers](./config-layers) | `core/config_manager.py` | YAML + SQLite + env merge strategy |
| [Security](./security) | `core/crypto.py` | Fernet encryption, secret management, masking |
