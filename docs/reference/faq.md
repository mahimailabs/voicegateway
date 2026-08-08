---
title: Frequently Asked Questions
description: Common questions about VoiceGateway covering production readiness, agent-framework integration, performance overhead, Kubernetes and metrics, S2S, MCP, custom voices, backups, and key rotation.
---
## Is VoiceGateway production-ready?

VoiceGateway is in alpha. It is suitable for development, staging, and low-to-medium traffic production workloads. The core routing, cost tracking, and fallback features are stable and covered by 200+ tests with 75%+ code coverage enforced by CI (`pyproject.toml` sets `fail_under = 75`). For high-traffic production, you should:

- Run thorough load tests against your specific workload
- Monitor the dashboard for latency and error rates
- Set up budget alerts with `budget_action: warn` before switching to `block`
- Pin the version in your `requirements.txt`

A stable 1.0 release is the goal once the API surface has been validated by the community.

---

## Can I use VoiceGateway with LangGraph or CrewAI?

Not as a wrapper. `attach()` and `guard()` target voice-agent runtimes: a LiveKit `AgentSession` or a Pipecat `PipelineTask`. LangGraph and CrewAI are text-LLM orchestration frameworks, so VoiceGateway does not sit inside their call path.

You can still track spend alongside them:

1. **Point the framework at providers directly** and read costs out of band from the [HTTP API](/api/http-api) (`/v1/costs`, `/v1/logs`) or the [MCP server](/mcp/index).
2. **For a voice agent**, build it on LiveKit or Pipecat and meter it with `attach()` as shown in the [quickstart](/get-started).

See [what you can profile](/guide/what-you-can-profile) for when VoiceGateway is the right fit. The MCP server's tools work with any agent framework that supports MCP (Claude Code, Cursor, Codex, Cline, etc.).

---

## What is the performance overhead?

VoiceGateway adds in-process middleware around each provider call: routing resolution from a config dict, an async SQLite write per logged request (cost + latency record), an async SQLite read on cache miss for the project budget check, and timestamp diffs for TTFB and total latency.

There is no extra network hop and no inter-process boundary. Cost-tracking writes are non-blocking; the budget check is cached in memory with a 30-second TTL so most requests do not hit the database. The latency floor for any voice agent is provider latency (typically 50ms-2000ms for STT, LLM, and TTS calls), and VG's middleware is designed to be a small fraction of that.

VG does not ship a benchmark suite. If you need a precise overhead number for your hardware and workload, run one against your stack rather than relying on a published figure.

---

## Can I run VoiceGateway on Kubernetes?

Yes. VoiceGateway is a standard Python application that works in any container orchestrator. A typical Kubernetes setup:

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: voicegateway
spec:
  replicas: 1  # SQLite requires single-writer
  template:
    spec:
      containers:
        - name: voicegateway
          image: voicegateway:latest
          ports:
            - containerPort: 8080  # API
            - containerPort: 9090  # Dashboard
          volumeMounts:
            - name: config
              mountPath: /app/voicegw.yaml
              subPath: voicegw.yaml
            - name: data
              mountPath: /data
          env:
            - name: VOICEGW_DB_PATH
              value: /data/voicegw.db
      volumes:
        - name: config
          configMap:
            name: voicegw-config
        - name: data
          persistentVolumeClaim:
            claimName: voicegw-data
```

**Important:** Since VoiceGateway uses SQLite, run a single replica for writes. If you need horizontal scaling, put a load balancer in front with sticky sessions, or embed VoiceGateway as a library within each worker process (each gets its own DB).

Note: with separate per-replica DBs, the in-memory budget cache does not sync across replicas. A project-wide daily budget cannot be strictly enforced across instances, only within each one. For project-wide budgets across multiple instances, single-instance is currently the only supported topology. A shared backend (Redis or PostgreSQL) is on the roadmap.

---

## Can I export metrics to Prometheus/Grafana?

VoiceGateway exposes a `GET /v1/metrics` endpoint that returns metrics in **Prometheus text format** (`text/plain`). You can scrape it directly with Prometheus:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: voicegateway
    static_configs:
      - targets: ['localhost:8080']
    metrics_path: /v1/metrics
```

Example response:

```
# HELP voicegw_uptime_seconds Process uptime
# TYPE voicegw_uptime_seconds gauge
voicegw_uptime_seconds 3421.5
# HELP voicegw_providers_configured Configured providers
# TYPE voicegw_providers_configured gauge
voicegw_providers_configured 5
# HELP voicegw_cost_usd_total USD cost summed over a ROLLING trailing 24 hours (now-86400s to now). This is a gauge despite the _total suffix: it DECREASES as requests age out of the window. It is not a since-start total and not a calendar day. Do not use rate() or increase() on it.
# TYPE voicegw_cost_usd_total gauge
voicegw_cost_usd_total{period="today"} 12.340000
# TYPE voicegw_requests_total gauge
voicegw_requests_total{provider="deepgram"} 142
```

For Grafana, point it at Prometheus and graph `voicegw_cost_usd_total{period="today"}` or `sum(voicegw_requests_total)` directly.

**They are gauges, so `rate()` and `increase()` do not apply.** Both series are sums over a rolling trailing 24 hours (`now - 86400`), not since-start totals and not a calendar day, so the value falls whenever a request ages out of the window. `rate()` and `increase()` require a monotonic counter and read every one of those decreases as a counter reset, which invents spend and traffic that never happened. The `_total` suffix and the `period="today"` label are misnomers kept so the published series names do not break; the `# TYPE` line is now `gauge`.

```promql
# Wrong
rate(voicegw_cost_usd_total[5m])

# Right
voicegw_cost_usd_total{period="today"}             # spend in the last 24h
delta(voicegw_cost_usd_total{period="today"}[1h])  # how that 24h figure moved
```

If you were already graphing these with `rate()` or `increase()`, replace the expression rather than the metric name: the names are unchanged. For a true monotonic total, use `GET /v1/costs?period=all`.

---

## Does VoiceGateway support speech-to-speech (S2S)?

Not directly. VoiceGateway routes STT, LLM, and TTS as separate modalities. For a speech-to-speech pipeline, you compose all three:

```python
from livekit.agents import AgentSession
from livekit.plugins import deepgram, openai, cartesia
from voicegateway import attach

session = AgentSession(
    stt=deepgram.STT(model="nova-3"),
    llm=openai.LLM(model="gpt-4o-mini"),
    tts=cartesia.TTS(model="sonic-3"),
)
attach(session, project="my-agent")  # meters STT, LLM, and TTS separately
```

This gives you full control over each stage, independent fallbacks, and per-modality cost tracking. Native S2S model support (e.g., GPT-4o audio) may be added in a future release.

---

## MCP vs function calling -- when do I use which?

**MCP (Model Context Protocol)** and **function calling** serve different purposes:

| | MCP | Function calling |
|---|---|---|
| **What it does** | Lets coding agents manage the gateway (add models, check costs, create projects) | Lets LLMs call functions during a conversation |
| **When to use** | Development workflow, CI/CD, infrastructure management | Runtime in your voice agent pipeline |
| **Who calls it** | Claude Code, Cursor, Codex, Cline | The LLM in your agent pipeline |
| **Transport** | stdio or HTTP/SSE | Provider-specific (OpenAI function calling, Anthropic tool use) |

Use VoiceGateway's MCP server to **manage** the gateway. Use function calling within your agent to **interact with users**.

---

## Can I use custom TTS voices?

Yes, through the provider's native voice configuration. Pass the voice id either as a `:suffix` on the model string or via the `voice` kwarg:

```python
from livekit.plugins import cartesia, elevenlabs, openai

# The voice id is native provider config. Pass the plugin into your
# AgentSession, then call attach(session) to meter it.
tts = cartesia.TTS(model="sonic-3", voice="your-voice-id")
tts = elevenlabs.TTS(voice="custom-voice-id")
tts = openai.TTS(voice="alloy")
```

Voice IDs are provider-specific:
- **Cartesia:** voice IDs from the [Cartesia dashboard](https://play.cartesia.ai/)
- **ElevenLabs:** voice IDs from your [ElevenLabs voice library](https://elevenlabs.io/)
- **OpenAI TTS:** voice names like `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
- **Kokoro/Piper:** model-specific voice configurations

---

## How do I back up my data?

VoiceGateway stores all data in a single SQLite database file (default: `~/.config/voicegateway/voicegw.db`).

The safe way to copy the database while the gateway is running is the SQLite `.backup` command. It is atomic and respects SQLite's locking protocol, so it produces a consistent snapshot even if a write is in flight:

```bash
sqlite3 ~/.config/voicegateway/voicegw.db ".backup ~/backups/voicegw-$(date +%Y%m%d).db"
```

`cp` is not safe by default. VoiceGateway does not enable WAL journaling, so a partial-write `cp` while the gateway is mid-transaction can produce a corrupt backup. `.backup` handles this correctly.

For automated backups:

```bash
# crontab
0 2 * * * sqlite3 ~/.config/voicegateway/voicegw.db ".backup /backups/voicegw-$(date +\%Y\%m\%d).db"
```

The database contains request logs, cost records, and project metadata. Configuration lives in `voicegw.yaml` (back that up separately).

---

## How do I rotate API keys?

API keys are read from environment variables via `${ENV_VAR}` references in `voicegw.yaml`. To rotate:

1. **Get a new key** from the provider's dashboard
2. **Update the environment variable:**
   ```bash
   export OPENAI_API_KEY=sk-new-key-here
   ```
3. **Restart VoiceGateway:**
   ```bash
   voicegw serve --port 8080
   ```

VoiceGateway reads environment variables at startup. No config file changes are needed if you use `${ENV_VAR}` references (which is the recommended approach).

For Docker deployments, update the environment variable in your `docker-compose.yml` or secrets manager and restart:

```bash
docker compose up -d
```

---

## Can I use VoiceGateway without LiveKit?

VoiceGateway is built on `livekit-agents` and returns LiveKit plugin instances. The core dependency on `livekit-agents` is required. However, you do not need a LiveKit server (rooms, WebRTC) to use VoiceGateway -- the Gateway, cost tracking, dashboard, and MCP server all work standalone.

If you are not using LiveKit for real-time transport, you can still benefit from:
- Unified cost tracking across STT/LLM/TTS providers
- Budget enforcement
- The web dashboard
- The MCP server for managing providers from your coding agent

---

## How many concurrent requests can VoiceGateway handle?

VoiceGateway itself is async and adds minimal overhead. The bottleneck is typically the upstream providers. Since VoiceGateway uses async/await throughout (FastAPI, aiosqlite, httpx), it can handle hundreds of concurrent requests on a single process.

For the SQLite storage layer, writes are serialized (one writer at a time), but this is rarely a bottleneck since each write takes ~1ms. If you need higher write throughput, you can:

- Disable cost tracking for non-critical workloads
- Use separate database files per process
- Switch to a different storage backend (PostgreSQL is on the roadmap; until then VG runs on a single instance for write workloads)

## Related pages

- [Troubleshooting](/reference/troubleshooting)
- [Quickstart](/get-started)
- [MCP Server](/mcp/index)
- [Changelog](https://github.com/mahimailabs/voicegateway/blob/main/CHANGELOG.md)
- [Contributing](/contributing/index)
