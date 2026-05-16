# Budget Enforcement

VoiceGateway supports per-project daily budgets with three enforcement modes: `warn`, `throttle`, and `block`. Budgets are enforced at request-completion time inside the cost tracker; the `inference` factories themselves never raise on budget. The `BudgetThrottleSignal` and `BudgetExceededError` types are available for callers that want to wire their own pre-request check (CLI / HTTP / dashboard).

## Configuration

```yaml
projects:
  warn-demo:
    name: Warn Demo
    daily_budget: 1.00
    budget_action: warn
    tags: [demo]
    providers:
      openai:
        api_key: ${OPENAI_API_KEY}
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}
      cartesia:
        api_key: ${CARTESIA_API_KEY}

  throttle-demo:
    name: Throttle Demo
    daily_budget: 1.00
    budget_action: throttle
    tags: [demo]
    providers:
      openai:
        api_key: ${OPENAI_API_KEY}
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}

  block-demo:
    name: Block Demo
    daily_budget: 1.00
    budget_action: block
    tags: [demo]
    providers:
      openai:
        api_key: ${OPENAI_API_KEY}
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}

providers:
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

cost_tracking:
  enabled: true
```

## Mode 1: Warn

The `warn` mode logs a warning when the budget is exceeded but allows all requests to proceed. Use this for visibility without disrupting service.

```python
from voicegateway.core.active_project import set_project
from voicegateway.inference import STT, LLM, TTS

set_project("warn-demo")
# Requests proceed even after budget is exceeded.
# Check your logs for: "Project 'warn-demo' exceeded daily budget: $X.XX / $1.00"
stt = STT("deepgram/nova-3")
llm = LLM("openai/gpt-4.1-mini")
tts = TTS("cartesia/sonic-3")
```

**Log output when budget is exceeded:**

```
WARNING - Project 'warn-demo' exceeded daily budget: $1.23 / $1.00
```

## Mode 2: Throttle (caller-driven)

The inference factories do not raise `BudgetThrottleSignal` themselves. Wire a pre-flight check in your worker if you want the throttle path:

```python
from voicegateway import inference
from voicegateway.inference._factory import get_gateway
from voicegateway.middleware.budget_enforcer import BudgetThrottleSignal


async def stt_for(project: str):
    gw = get_gateway()
    try:
        await gw._budget_enforcer.check_budget(project)
    except BudgetThrottleSignal:
        # Budget exceeded -- fall back to local Whisper.
        inference.set_project(project)
        return inference.STT("local/whisper-large-v3")
    inference.set_project(project)
    return inference.STT("deepgram/nova-3")
```

The `_budget_enforcer` reference is an internal handle today; v0.0.6 plans a public `inference.check_budget()` helper so callers no longer reach into a private attribute.

## Mode 3: Block (caller-driven)

```python
import asyncio

from voicegateway import inference
from voicegateway.inference._factory import get_gateway
from voicegateway.middleware.budget_enforcer import BudgetExceededError


async def main():
    gw = get_gateway()
    try:
        await gw._budget_enforcer.check_budget("block-demo")
    except BudgetExceededError as e:
        print(f"Request blocked: {e}")
        print(f"  Project: {e.project}")
        print(f"  Spent today: ${e.spent_usd:.2f}")
        print(f"  Daily budget: ${e.budget_usd:.2f}")
        # Handle gracefully -- show user a message, queue for later, etc.
    else:
        inference.set_project("block-demo")
        stt = inference.STT("deepgram/nova-3")


asyncio.run(main())
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

The throttle path can be paired with the manual chain walk pattern from [Fallback Chains](/examples/fallback-chains): on `BudgetThrottleSignal`, walk a chain that ends in a local model.

```yaml
projects:
  prod:
    daily_budget: 50.00
    budget_action: throttle
    providers:
      deepgram:
        api_key: ${DEEPGRAM_API_KEY}

fallbacks:
  stt:
    - deepgram/nova-3
    - local/whisper-large-v3
```

```python
async def stt_for(project: str):
    gw = get_gateway()
    try:
        await gw._budget_enforcer.check_budget(project)
    except BudgetThrottleSignal:
        # Walk the chain to land on the cheaper / local backup.
        return first_resolvable("stt", ["deepgram/nova-3", "local/whisper-large-v3"])
    return inference.STT("deepgram/nova-3")
```
