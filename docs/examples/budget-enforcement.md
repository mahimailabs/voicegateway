# Budget Enforcement

VoiceGateway supports per-project daily budgets with three enforcement modes: `warn`, `throttle`, and `block`. This example demonstrates all three.

## Configuration

```yaml
providers:
  openai:
    api_key: ${OPENAI_API_KEY}
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  cartesia:
    api_key: ${CARTESIA_API_KEY}
  ollama:
    base_url: http://localhost:11434

models:
  stt:
    deepgram/nova-3:
      provider: deepgram
      model: nova-3
    whisper/large-v3:
      provider: whisper
      model: large-v3
  llm:
    openai/gpt-4.1-mini:
      provider: openai
      model: gpt-4.1-mini
    ollama/qwen2.5:3b:
      provider: ollama
      model: qwen2.5:3b
  tts:
    cartesia/sonic-3:
      provider: cartesia
      model: sonic-3
      default_voice: 794f9389-aac1-45b6-b726-9d9369183238
    kokoro/default:
      provider: kokoro
      model: default

stacks:
  cloud:
    stt: deepgram/nova-3
    llm: openai/gpt-4.1-mini
    tts: cartesia/sonic-3
  local:
    stt: whisper/large-v3
    llm: ollama/qwen2.5:3b
    tts: kokoro/default

projects:
  warn-demo:
    name: Warn Demo
    daily_budget: 1.00
    budget_action: warn
    tags: [demo]

  throttle-demo:
    name: Throttle Demo
    daily_budget: 1.00
    budget_action: throttle
    tags: [demo]

  block-demo:
    name: Block Demo
    daily_budget: 1.00
    budget_action: block
    tags: [demo]

cost_tracking:
  enabled: true
```

## Mode 1: Warn

The `warn` mode logs a warning when the budget is exceeded but allows all requests to proceed. Use this for visibility without disrupting service.

```python
from voicegateway import Gateway

gw = Gateway()

# These requests proceed even after budget is exceeded.
# Check your logs for: "Project 'warn-demo' exceeded daily budget: $X.XX / $1.00"
stt = gw.stt("deepgram/nova-3", project="warn-demo")
llm = gw.llm("openai/gpt-4.1-mini", project="warn-demo")
tts = gw.tts("cartesia/sonic-3", project="warn-demo")
```

**Log output when budget is exceeded:**

```
WARNING - Project 'warn-demo' exceeded daily budget: $1.23 / $1.00
```

## Mode 2: Throttle

The `throttle` mode raises a `BudgetThrottleSignal` exception, signaling the caller to fall back to a cheaper (typically local) model stack.

```python
from voicegateway import Gateway
from voicegateway.middleware.budget_enforcer import BudgetThrottleSignal

gw = Gateway()


def get_stt(project: str) -> object:
    """Get STT with automatic fallback to local on budget exceed."""
    try:
        return gw.stt("deepgram/nova-3", project=project)
    except BudgetThrottleSignal:
        # Budget exceeded -- fall back to local Whisper
        print(f"Budget exceeded for {project}, falling back to local STT")
        return gw.stt("whisper/large-v3", project=project)


def get_llm(project: str) -> object:
    """Get LLM with automatic fallback to local on budget exceed."""
    try:
        return gw.llm("openai/gpt-4.1-mini", project=project)
    except BudgetThrottleSignal:
        print(f"Budget exceeded for {project}, falling back to local LLM")
        return gw.llm("ollama/qwen2.5:3b", project=project)


def get_tts(project: str) -> object:
    """Get TTS with automatic fallback to local on budget exceed."""
    try:
        return gw.tts("cartesia/sonic-3", project=project)
    except BudgetThrottleSignal:
        print(f"Budget exceeded for {project}, falling back to local TTS")
        return gw.tts("kokoro/default", project=project)


# Usage
stt = get_stt("throttle-demo")
llm = get_llm("throttle-demo")
tts = get_tts("throttle-demo")
```

### Using Stacks for Cleaner Fallback

```python
def get_stack(project: str):
    """Get the full model stack, falling back to local on budget exceed."""
    try:
        return gw.stack("cloud", project=project)
    except BudgetThrottleSignal:
        print(f"Budget exceeded for {project}, switching to local stack")
        return gw.stack("local", project=project)


stt, llm, tts = get_stack("throttle-demo")
```

## Mode 3: Block

The `block` mode raises a `BudgetExceededError` that rejects the request entirely. Use this for strict cost control.

```python
from voicegateway import Gateway
from voicegateway.middleware.budget_enforcer import BudgetExceededError

gw = Gateway()

try:
    stt = gw.stt("deepgram/nova-3", project="block-demo")
except BudgetExceededError as e:
    print(f"Request blocked: {e}")
    print(f"  Project: {e.project}")
    print(f"  Spent today: ${e.spent_usd:.2f}")
    print(f"  Daily budget: ${e.budget_usd:.2f}")
    # Handle gracefully -- show user a message, queue for later, etc.
```

**Output when budget is exceeded:**

```
Request blocked: Project 'block-demo' exceeded daily budget: $1.23 / $1.00
  Project: block-demo
  Spent today: $1.23
  Daily budget: $1.00
```

## Budget Status API

Check budget status before making a request:

```python
# Via the HTTP API
import httpx

resp = httpx.get("http://localhost:8080/v1/projects")
for project in resp.json()["projects"]:
    print(f"{project['id']}: {project['budget_status']}")
    # "ok", "warning" (>80% spent), or "exceeded"
```

The `BudgetEnforcer.get_budget_status()` method returns:

| Status | Condition |
|--------|-----------|
| `"ok"` | Under 80% of budget |
| `"warning"` | Between 80% and 100% of budget |
| `"exceeded"` | At or over 100% of budget |

## Cache Behavior

Budget checks are cached in memory with a **30-second TTL** to avoid hitting SQLite on every single request. This means:

- A budget may be briefly exceeded before the cache refreshes
- The maximum over-spend window is 30 seconds of requests
- The TTL is configurable via `BudgetEnforcer(cache_ttl_seconds=30.0)`

For high-throughput scenarios, this tradeoff between precision and performance is usually acceptable. If you need tighter enforcement, reduce the TTL:

```python
# In a custom Gateway subclass or direct instantiation
enforcer = BudgetEnforcer(config, storage, cache_ttl_seconds=5.0)
```

## Combining with Fallback Chains

For the best of both worlds, combine `throttle` mode with fallback chains:

```yaml
projects:
  prod:
    daily_budget: 50.00
    budget_action: throttle

fallbacks:
  stt:
    - deepgram/nova-3
    - whisper/large-v3
```

```python
try:
    stt = gw.stt("deepgram/nova-3", project="prod")
except BudgetThrottleSignal:
    # Fallback chain handles the failover
    stt = gw.stt_with_fallback(project="prod")
```
