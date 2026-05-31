# Middleware

The middleware layer sits between the Gateway and provider instances, providing cross-cutting concerns: cost tracking, latency monitoring, rate limiting, fallback chains, budget enforcement, and request logging.

## Middleware Components

```mermaid
graph TD
    subgraph Request["Incoming Request"]
        REQ["gateway.stt('deepgram/nova-3', project='prod')"]
    end

    subgraph Middleware["Middleware Pipeline"]
        BE["BudgetEnforcer<br/>check_budget()"]
        RL["RateLimiter<br/>acquire()"]
        FB["FallbackChain<br/>resolve()"]
        LG["RequestLogger<br/>log_request()"]
        IP["InstrumentedProvider<br/>wrap_provider()"]
        CT["CostTracker<br/>calculate_cost()"]
        LM["LatencyMonitor<br/>start() / finish()"]
    end

    subgraph Post["Post-Request"]
        REC["RequestRecord → SQLite"]
    end

    REQ --> BE
    BE -->|under budget| RL
    BE -->|over budget + block| ERR["BudgetExceededError"]
    BE -->|over budget + throttle| THR["BudgetThrottleSignal"]
    RL --> LG
    LG --> FB
    FB --> IP
    IP --> CT
    IP --> LM
    CT --> REC
    LM --> REC
```

## Execution Order

When a request flows through the Gateway:

| Step | Component | Action |
|------|-----------|--------|
| 1 | **BudgetEnforcer** | Checks project's daily spend against its budget |
| 2 | **RateLimiter** | Ensures provider hasn't exceeded RPM limit |
| 3 | **RequestLogger** | Logs the incoming request |
| 4 | **FallbackChain** | Tries primary model, falls back on failure |
| 5 | **Router** | Resolves model ID to provider instance |
| 6 | **InstrumentedProvider** | Wraps the instance to record metrics |
| 7 | **CostTracker** | Calculates cost when the request completes |
| 8 | **LatencyMonitor** | Records TTFB and total latency |

## BudgetEnforcer

**File:** `src/voicegateway/middleware/budget_enforcer.py`

Enforces per-project daily spending limits. Budget checks are cached in memory with a **30-second TTL** to avoid hitting the database on every request.

### Three Modes

| Mode | `budget_action` | Behavior |
|------|-----------------|----------|
| **Warn** | `"warn"` | Logs a warning, allows the request to proceed |
| **Throttle** | `"throttle"` | Raises `BudgetThrottleSignal` -- caller should fall back to local models |
| **Block** | `"block"` | Raises `BudgetExceededError` -- request is rejected |

```python
class BudgetEnforcer:
    def __init__(self, config, storage, cache_ttl_seconds=30.0):
        self._cache: dict[str, tuple[float, float]] = {}

    async def check_budget(self, project: str) -> None:
        pcfg = self._get_project_config(project)
        if pcfg is None or pcfg.daily_budget <= 0:
            return  # No budget configured = unlimited

        today_spend = await self._get_today_spend(project)
        if today_spend < pcfg.daily_budget:
            return  # Under budget

        if pcfg.budget_action == "warn":
            logger.warning(...)
        elif pcfg.budget_action == "throttle":
            raise BudgetThrottleSignal(project, today_spend, pcfg.daily_budget)
        elif pcfg.budget_action == "block":
            raise BudgetExceededError(project, today_spend, pcfg.daily_budget)
```

The `get_budget_status()` method returns a status string for API responses: `"ok"`, `"warning"` (>80% spent), or `"exceeded"`.

## CostTracker

**File:** `src/voicegateway/middleware/cost_tracker.py`

Calculates per-request costs based on the pricing catalog and writes request records to SQLite.

### Pricing

Costs are delegated to `voice-prices`. The cost tracker maps the recorded
units onto a `voice_prices.Usage` per modality (STT: `audio_input_seconds`,
LLM: `input_tokens` / `output_tokens` / `cache_read_tokens`, TTS:
`characters`) and calls `voice_prices.calc_price`. Self-hosted `local/*` and
`ollama/*` models price at `$0`. See `voicegateway.inference.pricing.catalog`.

### Key Methods

- **`CostTracker.calculate_cost(model_id, modality, input_units, output_units, cached_input_units)`** -- returns cost in USD (0.0 for unknown or self-hosted)
- **`create_record(...)`** -- creates a `RequestRecord` with cost, latency, and metadata
- **`log_request(record)`** -- persists the record to SQLite (async)

## LatencyMonitor

**File:** `src/voicegateway/middleware/latency_monitor.py`

Tracks two timing metrics:

- **TTFB (Time to First Byte):** measured from request start to the first result/token
- **Total latency:** measured from request start to completion

```python
class LatencyMonitor:
    def __init__(self, ttfb_warning_ms: float = 500.0):
        self._ttfb_warning_ms = ttfb_warning_ms

    def start(self) -> _LatencyTimer:
        return _LatencyTimer(self._ttfb_warning_ms)
```

The `_LatencyTimer` logs a warning when TTFB exceeds the configured threshold (default 500ms). This threshold is configurable via `latency.ttfb_warning_ms` in `voicegw.yaml`.

## RateLimiter

**File:** `src/voicegateway/middleware/rate_limiter.py`

A sliding-window rate limiter using a token bucket pattern, enforced per provider.

```yaml
# voicegw.yaml
rate_limits:
  openai:
    requests_per_minute: 60
  deepgram:
    requests_per_minute: 100
```

```python
class RateLimiter:
    async def acquire(self, provider: str) -> None:
        """Raises RateLimitExceeded if the provider's RPM limit is hit."""
```

The limiter maintains a list of timestamps for each provider. On each `acquire()` call, it removes entries older than 60 seconds and checks whether the count exceeds the configured RPM. Uses `asyncio.Lock` for thread safety.

## Resolver-time fallback (manual walk)

VoiceGateway does not run an automatic fallback middleware.
Resolver-time fallback is a startup-walk pattern: enumerate the
chain and call the matching `voicegateway.inference.STT/LLM/TTS`
factory until one succeeds, then pass the resolved instance to
`AgentSession`. The chain lives in `voicegw.yaml` under
`fallbacks:` and is documentation-only at runtime.

```yaml
# voicegw.yaml
fallbacks:
  stt:
    - deepgram/nova-3
    - openai/whisper-1
    - local/whisper-large-v3
  tts:
    - cartesia/sonic-3
    - elevenlabs/turbo-v2.5
```

```python
def first_resolvable_stt(chain):
    for model_id in chain:
        try:
            return inference.STT(model_id)
        except Exception:
            continue
    raise RuntimeError("every STT model in the chain failed to resolve")
```

Once that resolved model is wired into `AgentSession`, the call uses it for its lifetime: VG does not swap providers mid-call. For runtime / mid-call failover, compose LiveKit's `FallbackAdapter` around VG `inference.*` instances directly; see the [LiveKit FallbackAdapter integration](/examples/livekit-fallback-adapter) guide.

## RequestLogger

**File:** `src/voicegateway/middleware/logger.py`

Structured logging for all gateway operations under the `gateway.requests` logger name.

| Method | Log Level | Format |
|--------|-----------|--------|
| `log_request(model_id, modality)` | INFO | `[STT] deepgram/nova-3` |
| `log_response(model_id, modality, latency_ms, cost_usd)` | INFO | `[STT] deepgram/nova-3 -> success (142ms, $0.000430)` |
| `log_fallback(original, fallback, reason)` | WARNING | `[FALLBACK] deepgram/nova-3 -> openai/whisper-1 (reason: ...)` |
| `log_error(model_id, error)` | ERROR | `[ERROR] deepgram/nova-3: Connection timeout` |

## InstrumentedProvider

**File:** `src/voicegateway/middleware/instrumented_provider.py`

Transparent proxy wrappers that record TTFB, total latency, and cost without changing the provider's API surface.

### How It Works

```mermaid
graph LR
    A["User code: stt.transcribe()"] --> B["InstrumentedSTT.__getattr__('transcribe')"]
    B --> C["getattr(wrapped_stt, 'transcribe')"]
    C --> D["Actual Deepgram STT.transcribe()"]
    D --> E["_mark_first_byte()"]
    E --> F["_log_request() → CostTracker → SQLite"]
```

The three wrapper classes (`InstrumentedSTT`, `InstrumentedLLM`, `InstrumentedTTS`) extend `_InstrumentedBase`, which:

1. Uses `object.__setattr__` in `__init__` to store internal state without triggering the proxy
2. Implements `__getattr__` to delegate all attribute access to the wrapped instance
3. Implements `__setattr__` to delegate attribute writes to the wrapped instance
4. Records `_start_time` at construction via `time.perf_counter()`
5. Provides `_mark_first_byte()` to record TTFB
6. Provides `_log_request()` to write a `RequestRecord` to storage (with a `_logged` guard to prevent duplicates)

The wrapping is applied by the Gateway's `_wrap()` method and can be disabled by setting `observability.latency_tracking: false` in config.
