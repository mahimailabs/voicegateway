# 0033: LiveKit's own uploader is the default agent connection

- Status: accepted (Mahimai, 2026-10-21)
- Date: 2026-10-21

## Context

Decision 0002 made `attach()` in a Python SDK the way an agent reports to VoiceGateway, and 0014 added LiveKit's observability endpoints beside it. Both LiveKit Agents runtimes now upload by themselves when `LIVEKIT_OBSERVABILITY_URL` is set, keeping the URL's path prefix:

- Python, 1.8.6 and later: `livekit-agents/livekit/agents/job.py`, `_observability_url` returns the variable when set (livekit/agents commit de40bb25, read 2026-10-21 through the LiveKit Docs MCP).
- Node: `agents-js` `job.ts` honours `LIVEKIT_OBSERVABILITY_URL` (commit e3babc6c, read 2026-10-21 through the LiveKit Docs MCP).

Both post OTLP traces, logs and metrics and the session report to that URL. Project-scoped ingestion (0032) already serves those paths under `/ingest/<organisation>/<project>`.

The uploader cannot do three things: keep LiveKit Cloud's own observability (the variable redirects the upload away from it), carry the team's own fields on a call, or report while the call is still running (the session report arrives at its end).

## Options

1. `attach()` as the only agent connection, as 0002 planned: every LiveKit user installs and calls our code, and we rebuild metrics LiveKit already computes.
2. The uploader only: one variable, no VoiceGateway import, but the three gaps above stay open.
3. The uploader as the default connection, and `attach()` as an optional add-on for the three gaps.

## Decision

Option 3. LiveKit Agents' uploader (`LIVEKIT_OBSERVABILITY_URL`) is the default agent connection. A Python SDK, `attach()`, is an optional add-on for what the uploader cannot do: keep LiveKit Cloud's own observability, custom call fields, and live mid-call events. Python first; TypeScript, Go and others as needed (Mahimai, 2026-10-21). 0002 stands for the SDK's shape.

## Consequences

- Onboarding needs no SDK: one variable and a recording session. `examples/livekit-agent` is the documented, runnable reference.
- The SDK is thin and the wire contract is the product: every language SDK maps its framework's events to the same versioned batch, and the engine does the joining (docs/specs/python-sdk.md).
- When both run, the upload stays the source for spans, recording and turn numbers; the SDK adds fields and live events, and nothing is counted twice.
- The upload paths and payloads stay a contract tracked per LiveKit Agents release (0014); the example pins the tested version.
- Recording is the switch for the upload, but not for the audio: `record={"audio": False}` (Python `RecordingOptions`, in releases before 1.8.6; Node `record: { audio: false }`, agents-js PR #1702, before its 1.8.0 uploader) sends traces, logs, metrics and the transcript without the call audio. Read 2026-10-21 in livekit-agents 1.8.8 (`agent_session.py`, `telemetry/traces.py`) and agents-js e3babc6c. Teams that will not upload call audio therefore need no SDK.
- The console shows both ingestion URLs and whether each source last connected or was refused, with the reason (docs/specs/first-call-setup.md).
