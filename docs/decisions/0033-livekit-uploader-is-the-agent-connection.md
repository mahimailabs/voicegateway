# 0033: LiveKit's own uploader is the agent connection

- Status: accepted
- Date: 2026-10-21

## Context

Decision 0002 made `attach()` in a Python SDK the way an agent reports to VoiceGateway, and 0014 added LiveKit's observability endpoints beside it. Both LiveKit Agents runtimes now upload by themselves when `LIVEKIT_OBSERVABILITY_URL` is set, keeping the URL's path prefix:

- Python, 1.8.6 and later: `livekit-agents/livekit/agents/job.py`, `_observability_url` returns the variable when set (livekit/agents commit de40bb25, read 2026-10-21 through the LiveKit Docs MCP).
- Node: `agents-js` `job.ts` honours `LIVEKIT_OBSERVABILITY_URL` (commit e3babc6c, read 2026-10-21 through the LiveKit Docs MCP).

Both post OTLP traces, logs and metrics and the session report to that URL. Project-scoped ingestion (0032) already serves those paths under `/ingest/<organisation>/<project>`. Shipping an SDK package for V1 would add a release train, a dependency in every user's agent and a second path for the same data.

## Options

1. Ship `attach()` in V1 as 0002 planned: covers frameworks without an uploader, but every LiveKit user installs and calls our code, and we rebuild metrics LiveKit already computes.
2. Make LiveKit's uploader the agent connection: one variable in an existing agent, no VoiceGateway import, Python and Node alike. The "SDK" is the scoped URL, LiveKit's uploader and a documented runnable example.

## Decision

Option 2. V1 ships no `attach()` package. An agent connects by setting `LIVEKIT_OBSERVABILITY_URL` to its project's scoped URL; `examples/livekit-agent` is the documented, runnable reference.

## Consequences

- Amends 0002: `sdk/python` is not part of V1. It returns only through a later decision, if a gap appears that LiveKit's upload cannot fill (for example a framework other than LiveKit Agents).
- ARCHITECTURE.md drops the SDK component, the SDK row of the stack, `sdk/python/` from the layout and the "SDK adds under 1 ms per turn" target. Data flow step 1 names LiveKit's uploader.
- The upload paths and payloads stay a contract tracked per LiveKit Agents release (0014); the example pins the tested version.
- The console shows both ingestion URLs and whether each source last connected or was refused, with the reason (docs/specs/first-call-setup.md).
