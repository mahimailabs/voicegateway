# voicegateway

`attach(session)` for [LiveKit Agents](https://docs.livekit.io/agents/): sends an `AgentSession`'s events to [VoiceGateway](https://voicegateway.dev).

LiveKit Agents' own uploader (`LIVEKIT_OBSERVABILITY_URL`) is the default way to connect an agent. Use this SDK for what the uploader cannot do:

- Send to VoiceGateway without `LIVEKIT_OBSERVABILITY_URL`, so LiveKit Cloud's own observability stays.
- Put your own fields on a call, filterable in the call list.
- Report user and agent state while the call is still running.

## Install

```bash
uv add voicegateway
```

Python 3.11 to 3.13, livekit-agents 1.8.6 or later.

## Attach

```python
import voicegateway


@server.rtc_session(agent_name="my-agent")
async def entrypoint(ctx: agents.JobContext) -> None:
    session = AgentSession(...)
    vg = voicegateway.attach(session, fields={"customer": "acme"})
    await session.start(room=ctx.room, agent=MyAgent())
    vg.set_field("plan", "trial")
```

Call `attach` in the job entrypoint, before `session.start`. It reads:

- `VOICEGATEWAY_URL`: the project's agent upload URL from the console's **Connect your agent** panel, `https://<host>/ingest/<org>/<project>`. Or pass `url=`.
- `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET`: the agent's own LiveKit credentials, which sign the request.

The SDK flushes on the session's `close` event and on job shutdown. `await vg.close()` flushes and stops earlier; it takes at most 2 s.

## Fields

Keys match `^[a-z0-9_.-]{1,64}$`; values are strings of at most 256 characters. Later values win. An invalid key or value is dropped and counted on `vg.dropped_fields`; it never raises. A call holds at most 64 fields: VoiceGateway drops new keys past that, and a key the call already has still takes a new value.

## What it sends

`user_state_changed`, `agent_state_changed`, `conversation_item_added` (messages, with each message's own latency metrics), `session_usage_updated` (per-model usage), `function_tools_executed`, `error` and `close`, plus your fields, as batches of the versioned contract in [`sdk/contract/v1.schema.json`](../contract/v1.schema.json). It reads the job id and room from the job context. It never patches LiveKit.

## Fails open

The agent never waits on VoiceGateway and nothing raises into it.

- Missing URL, credentials or job context: one warning, and `attach` returns an attachment that does nothing.
- Events wait in a bounded queue (10,000) and are posted every second or every 100 events, gzipped.
- A 429, a 5xx or a network error is retried with backoff; any other 4xx drops the batch. A full queue, a refused batch or a close that runs past 2 s drops events and counts them on `vg.dropped`.

## What it does not do

- Spans or the call recording: those come only from the uploader. With both on, the upload stays the source for spans, recording and turns, and nothing is counted twice.
- Frameworks other than LiveKit Agents.

## Develop

```bash
cd sdk/python
uv sync --locked
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```
