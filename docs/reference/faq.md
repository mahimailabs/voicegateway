# Frequently Asked Questions

## Is VoiceGateway production-ready?

VoiceGateway is currently at v0.1.0 (alpha). It is suitable for development, staging, and low-to-medium traffic production workloads. The core routing, cost tracking, and fallback features are stable and covered by 200+ tests with over 70% code coverage. For high-traffic production, you should:

- Run thorough load tests against your specific workload
- Monitor the dashboard for latency and error rates
- Set up budget alerts with `budget_action: warn` before switching to `block`
- Pin the version in your `requirements.txt`

We aim for a stable v1.0 release once the API surface has been validated by the community.

---

## Can I use VoiceGateway with LangGraph or CrewAI?

Yes, but with a caveat. VoiceGateway returns LiveKit plugin instances from `gw.llm()`, which are designed for LiveKit agent pipelines. If you want to use VoiceGateway's cost tracking and routing with LangGraph or CrewAI:

1. **Use the HTTP API** -- call `/v1/models` to get available models, then route through VoiceGateway's server
2. **Use the Gateway directly** -- call `gw.llm()` to get a configured LLM instance, then extract the underlying client for your framework
3. **Use cost tracking only** -- point LangGraph/CrewAI at the providers directly, and use VoiceGateway's MCP server to track costs separately

The MCP server's 17 tools work with any agent framework that supports MCP (Claude Code, Cursor, Codex, Cline, etc.).

---

## What is the performance overhead?

VoiceGateway adds minimal overhead to provider calls:

- **Routing resolution:** microseconds (in-memory dict lookup)
- **Cost tracking:** ~1ms per request (async SQLite write)
- **Budget check:** ~1ms per request (async SQLite read)
- **Latency monitoring:** nanoseconds (timestamp diff)

The total overhead is typically under 5ms per request, which is negligible compared to provider latency (50ms-2000ms for most API calls). All middleware operations are async and non-blocking.

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

**Important:** Since VoiceGateway uses SQLite, run a single replica for writes. If you need horizontal scaling, put a load balancer in front with sticky sessions, or use the Gateway as a library within each worker process (each gets its own DB).

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
# HELP voicegw_cost_usd_total Total cost in USD (today)
# TYPE voicegw_cost_usd_total counter
voicegw_cost_usd_total{period="today"} 12.340000
voicegw_requests_total{provider="deepgram"} 142
```

For Grafana, point it at Prometheus and query `voicegw_cost_usd_total` or `voicegw_requests_total`.

---

## Does VoiceGateway support speech-to-speech (S2S)?

Not directly. VoiceGateway routes STT, LLM, and TTS as separate modalities. For a speech-to-speech pipeline, you compose all three:

```python
gw = Gateway()

stt = gw.stt("deepgram/nova-3", project="s2s-app")
llm = gw.llm("openai/gpt-4o-mini", project="s2s-app")
tts = gw.tts("cartesia/sonic-3", project="s2s-app")

# Use in a LiveKit AgentSession for real-time S2S
session = AgentSession(stt=stt, llm=llm, tts=tts)
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

Yes, through the provider's native voice configuration. Pass the voice ID in the `voice` parameter:

```python
tts = gw.tts("cartesia/sonic-3", voice="your-voice-id", project="my-app")
tts = gw.tts("elevenlabs/eleven_turbo_v2_5", voice="custom-voice-id", project="my-app")
```

Voice IDs are provider-specific:
- **Cartesia:** voice IDs from the [Cartesia dashboard](https://play.cartesia.ai/)
- **ElevenLabs:** voice IDs from your [ElevenLabs voice library](https://elevenlabs.io/)
- **OpenAI TTS:** voice names like `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
- **Kokoro/Piper:** model-specific voice configurations

---

## How do I back up my data?

VoiceGateway stores all data in a single SQLite database file (default: `~/.config/voicegateway/voicegw.db`). To back up:

```bash
# Find the database path
echo $VOICEGW_DB_PATH
# Or check your config's cost_tracking.db_path

# Copy the file (safe while gateway is running -- SQLite uses WAL mode)
cp ~/.config/voicegateway/voicegw.db ~/backups/voicegw-$(date +%Y%m%d).db
```

For automated backups:

```bash
# Add to crontab
0 2 * * * cp ~/.config/voicegateway/voicegw.db /backups/voicegw-$(date +\%Y\%m\%d).db
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
- Switch to a different storage backend (PostgreSQL support is planned)

## Related pages

- [Troubleshooting](/reference/troubleshooting)
- [Quick Start](/guide/quick-start)
- [MCP Server](/mcp/)
- [Changelog](/reference/changelog)
- [Contributing](/contributing/)
