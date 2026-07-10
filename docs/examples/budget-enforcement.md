---
title: Budget Enforcement
description: Enforce a daily spend cap using guard(llm, budget="$5.00/day") with warn, throttle, and block modes.
---

# Budget Enforcement

Use `guard()` to attach a daily budget to any provider. When the budget is exceeded, `guard()` can warn and continue, throttle to a cheaper fallback, or block the request outright.

## The three modes

```python
from livekit.plugins import openai
from voicegateway import guard

# warn: log and continue (default when you pass budget= without budget_action)
llm_warn = guard(
    openai.LLM(model="gpt-4o-mini"),
    budget="$5.00/day",
    project="warn-demo",
)

# throttle: fall back to a cheaper provider when the budget is exceeded
llm_throttle = guard(
    openai.LLM(model="gpt-4o-mini"),
    fallback=[openai.LLM(model="gpt-4o-mini")],   # swap in a local/cheaper model
    budget="$5.00/day",
    project="throttle-demo",
)

# block: raise BudgetExceededError when the budget is exceeded
llm_block = guard(
    openai.LLM(model="gpt-4o-mini"),
    budget="$5.00/day",
    project="block-demo",
)
```

<Note>
`guard()` returns the same type as the provider you pass in, so it is a
drop-in replacement anywhere you use the provider directly.
</Note>

## Configuration

Set the budget and action per project in `voicegw.yaml`:

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

## Mode 1: warn

The `warn` mode logs a warning when the budget is exceeded but allows all requests to proceed. Use this for visibility without disrupting service.

**Log output when budget is exceeded:**

```
WARNING - Project 'warn-demo' exceeded daily budget: $1.23 / $1.00
```

## Mode 2: throttle

With `fallback=` set, `guard()` switches to the fallback provider when the budget is exceeded instead of raising:

```python
from livekit.plugins import openai
from livekit.plugins import silero
from voicegateway import attach, guard


async def entrypoint(ctx):
    from livekit.agents import Agent, AgentSession

    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=...,
        llm=guard(
            openai.LLM(model="gpt-4o-mini"),
            fallback=[openai.LLM(model="gpt-4o-mini")],  # local or cheaper model
            budget="$1.00/day",
            project="throttle-demo",
        ),
        tts=...,
    )

    attach(session, project="throttle-demo")
    await session.start(agent=Agent(instructions="Be concise."), room=ctx.room)
```

## Mode 3: block

When the budget is exhausted, `guard()` raises `BudgetExceededError`. Catch it in your worker:

```python
import asyncio

from livekit.plugins import openai
from voicegateway import attach, guard
from voicegateway.middleware.budget_enforcer import BudgetExceededError


async def main():
    guarded_llm = guard(
        openai.LLM(model="gpt-4o-mini"),
        budget="$1.00/day",
        project="block-demo",
    )
    try:
        # guard() raises BudgetExceededError when the cap is hit.
        result = await guarded_llm.chat(...)
    except BudgetExceededError as e:
        print(f"Request blocked: {e}")
        print(f"  Project: {e.project}")
        print(f"  Spent today: ${e.spent_usd:.2f}")
        print(f"  Daily budget: ${e.budget_usd:.2f}")
        # Handle gracefully: show user a message, queue for later, etc.


asyncio.run(main())
```

**Output when budget is exceeded:**

```
Request blocked: Project 'block-demo' exceeded daily budget: $1.23 / $1.00
  Project: block-demo
  Spent today: $1.23
  Daily budget: $1.00
```

## Budget status via the HTTP API

Check budget status before making a request:

```python
import httpx

resp = httpx.get("http://localhost:8080/v1/projects")
for project in resp.json()["projects"]:
    print(f"{project['id']}: {project['budget_status']}")
    # "ok", "warning" (>80% spent), or "exceeded"
```

| Status | Condition |
|--------|-----------|
| `"ok"` | Under 80% of budget |
| `"warning"` | Between 80% and 100% of budget |
| `"exceeded"` | At or over 100% of budget |

## Cache behavior

Budget checks are cached in memory with a **30-second TTL** to avoid hitting SQLite on every single request. A budget may be briefly exceeded before the cache refreshes. For high-throughput scenarios this tradeoff is usually acceptable.

## Combining with fallback chains

The throttle path pairs naturally with the chain walk pattern from [Fallback Chains](/examples/fallback-chains). Pass a local or cheaper model as the `fallback=` to `guard()` so throttled calls still resolve:

```yaml
providers:
  deepgram:
    api_key: ${DEEPGRAM_API_KEY}
  ollama:
    base_url: http://localhost:11434
  whisper: {}
  kokoro: {}

projects:
  prod:
    daily_budget: 50.00
    budget_action: throttle
```
