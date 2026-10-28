# Python SDK: attach() for LiveKit Agents

## Goal

`voicegateway.attach(session)` gives what LiveKit Agents' uploader cannot (decision 0033):

1. VoiceGateway data without `LIVEKIT_OBSERVABILITY_URL`, so LiveKit Cloud's own observability stays.
2. Custom call fields: `attach(session, fields={...})` and `attachment.set_field(key, value)` mid-call, shown on the call and filterable in the call list.
3. Live mid-call state (user and agent state) for the live view.

With the uploader also pointed at VoiceGateway, the upload stays the source for spans, recording and turns; the SDK adds fields and live state, and nothing is counted twice.

## Context

- Decisions 0002 (attach() shape), 0012 (call identity), 0014 (uploader), 0032 (scoped ingestion), 0033 (uploader by default, SDK optional).
- docs/specs/first-call-setup.md: the connection recorder and Connect panel this adds a source to.

## The wire contract is the product

Every language SDK sends the same versioned batch; the engine does all joining and turn building, so SDKs stay thin. The contract is the JSON Schema `sdk/contract/v1.schema.json`, and the example batches in `sdk/contract/examples/` (valid and invalid) are conformance fixtures: the engine validates every batch against the schema, and its handler tests and every SDK's tests run the same examples. A JSON Schema, not openapi.yaml, because the latter is the console read contract and ingestion is not part of it; any language can validate against the schema.

`POST {VOICEGATEWAY_URL}/sdk/v1/events`, where `VOICEGATEWAY_URL` is the project's scoped base `https://<host>/ingest/<org>/<project>`. JSON, optionally gzip (`Content-Encoding: gzip`). `Authorization: Bearer <JWT>` signed with the agent's own LiveKit API key and secret, with the observability write grant, so no new credential and 0032's scoped check applies.

```json
{
  "version": 1,
  "batchId": "uuid, unique per batch; a retry resends the same id",
  "sdk": { "name": "voicegateway-python", "version": "1.0.0rc6" },
  "framework": { "name": "livekit-agents", "version": "1.8.8" },
  "call": { "jobId": "AJ_...", "roomName": "...", "roomSid": "RM_..." },
  "events": [ { "type": "...", "at": "RFC 3339 time", "data": { } } ]
}
```

`call` needs a `roomSid` or a `jobId`. At most 1,000 events and 1 MiB decompressed per batch. Event types and `data`:

| type | data | from LiveKit Agents |
| --- | --- | --- |
| `state` | `actor` (`user` or `agent`), `state` (the framework's state name) | `user_state_changed`, `agent_state_changed` |
| `message` | `role`, `text` (nullable), `interrupted` (nullable), `metrics` (the framework's per-message report as given, nullable) | `conversation_item_added` (`ChatMessage.metrics`) |
| `usage` | `models`: the session's usage so far, per model, as reported (cumulative) | `session_usage_updated` |
| `tool` | `name`, `error` (nullable) | `function_tools_executed` |
| `error` | `source`, `message`, `recoverable` (nullable) | `error` |
| `fields` | `fields`: string keys `^[a-z0-9_.-]{1,64}$`, string values up to 256 characters | `attach(fields=...)`, `set_field` |
| `close` | `reason` (nullable) | `close` |

An unknown event type is stored and ignored, so an older engine accepts a newer SDK.

## In Scope

Engine (Go):

1. `POST /ingest/{org}/{project}/sdk/v1/events` in internal/ingest: scoped resolver, LiveKit JWT verification, observability grant, quota, connection recorder with a new source `sdk` (Connect panel shows it). Body validated against the batch shape; refused bodies answer 400 and append nothing. Appended as kind `voicegateway.sdk.events` with the batch id as event id, so a retried batch is stored once.
2. `event.CallIdentities` for the new kind (room SID, job id), so quota continuation works.
3. The worker joins a batch to its call like a session report (room SID, else job id), opening the call by room SID when none exists yet. `fields` events merge into `calls.fields` (Postgres 00020, jsonb, GIN index), written only by the worker.
4. Turns: when a call has no `agent_turn` spans, each SDK assistant `message` is a turn, with the user message before it, timed by the framework's per-message metrics (end of turn and transcription delay from the user message; LLM first token and TTS first byte from the assistant message), and the call's usage is the latest `usage` event; when it has spans, the spans and their usage win. E2E latency, reported only on spans, stays unknown. LiveKit Agents deprecated `metrics_collected` in 1.8.8 in favour of `ChatMessage.metrics` and `session_usage_updated`, so the SDK reads those.
5. Reads: getCall returns `fields`; listCalls takes repeated `field=key:value` (all must match); a call's latest SDK `state` per actor is returned by getCall as `live` for the live view. Contract in openapi.yaml with x-source; `make api-types`.
6. Console: fields on the call page and a field filter in the call list; the live state on the call; the Connect panel's `sdk` source. Pre-built components only.

SDK (`sdk/python`, PyPI name `voicegateway`, versioned with the engine's 1.0.0 previews: `1.0.0rc6` is `1.0.0-preview.6` in PEP 440 form):

7. Public API only: `attach(session, *, url=None, fields=None) -> Attachment`, `Attachment.set_field(key, value)`, `Attachment.close()`, `__version__`. `import voicegateway` imports no LiveKit module.
8. Subscribes to AgentSession events only, plus job id and room name and SID from the job context; never patches LiveKit internals.
9. Bounded in-process queue; one background asyncio task flushes every 1 s or 100 events; gzip JSON POST through the HTTP client livekit-agents already depends on; JWT through `livekit.api.AccessToken`. Fails open: a full queue drops and counts; HTTP errors retry with backoff, then drop and count; nothing raises into the agent. Drains on close within 2 s.
10. Python 3.11 to 3.13; uv exact lock; ruff, ruff format, mypy strict, pytest; pre-commit scoped to sdk/python. Runtime dependencies as compatible ranges.
11. `examples/livekit-agent` gets an attach variant; site/docs a Python SDK page.

## Out of Scope

- Publishing to PyPI (needs Mahimai's separate go).
- TypeScript, Go and other SDKs (the fixtures are ready for them).
- Pushing live events over pub/sub: the live feed stays a refresh hint (0007); state is read on refresh.

## Acceptance Checks

1. `import voicegateway` loads no LiveKit module (pytest, `sys.modules`).
2. A LiveKit test session with `attach()` delivers state, message, metrics and tool events matching the fixtures to a fake endpoint; with the endpoint down it completes with no exception and the drop counter set.
3. Benchmark: under 1 ms per event on the agent's side.
4. Go: TestSdkBatches: every fixture is accepted and appended once (a retry by batch id is stored once); a malformed batch is 400; A's key at B's URL is refused and recorded on B only; quota cap 402.
5. Real call, SDK only (no `LIVEKIT_OBSERVABILITY_URL`): call stored with turns, the custom field on the call, live states readable.
6. Real call, SDK plus uploader: spans and recording from the upload, fields from the SDK, turn count not doubled.
7. The example's attach variant and the site/docs Python SDK page exist.
8. PyPI publish is not part of this.
