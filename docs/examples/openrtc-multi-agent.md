# OpenRTC: track every agent in one worker

[OpenRTC](https://github.com/mahimailabs/openrtc-runtime) runs N LiveKit voice
agents in one worker. VoiceGateway plugs into its `SessionObserver` seam, so a
single line gives every session per-call cost tracking, attributed per agent
and per tenant.

## Install

```bash
uv add "voicegateway[openrtc]"
```

## One line: hand the pool an observer

```python
from openrtc import AgentPool
from voicegateway.openrtc import VoiceGatewayObserver

pool = AgentPool(
    observers=[
        VoiceGatewayObserver(
            project="prod",
            collector_url="https://collector.example.com",  # your fleet collector
            api_key="<your-api-key>",
        )
    ],
)
pool.add("restaurant", RestaurantAgent, stt=..., llm=..., tts=...)
pool.add("dental", DentalAgent, stt=..., llm=..., tts=...)
pool.run()
```

You do not touch your `Agent` subclasses, tools, or session wiring. The
observer attaches VoiceGateway to each live session automatically.

## What gets recorded

Per call, rows are tagged:

- `project`: the value you passed to the observer.
- `agent_id`: which registered agent handled the call (`info.agent_name`).
- `tenant_id`: `info.metadata["tenant"]` if present in the room or job
  metadata, else `None`.

## Where the numbers go

- **Fleet mode** (shown above, `collector_url` set): one shared
  `RemoteCollectorSink` batches rows and POSTs them to
  `<collector_url>/v1/ingest` with `Authorization: Bearer <api_key>`. Every
  worker pushes to the same collector, so you slice cost by `agent_id`,
  `project`, and `tenant` across the whole fleet.
- **Single node** (omit `collector_url`): rows write to a local SQLite database
  (`db_path`, else `VOICEGW_DB_PATH`, else the default). Run `voicegw dashboard`
  to read it.

## Multi-tenant attribution

Dispatch jobs with room or job metadata like `{"tenant": "acme-corp"}`. OpenRTC
parses and merges it, and the observer reads `tenant` per call, so one agent
serving many tenants produces cleanly separated cost streams with no branching
in your code.

## How it works

The observer builds one sink lazily on the first session and shares it for the
worker's life. It holds only configuration, so it is safe to use with OpenRTC
`process` isolation (it pickles cleanly and rebuilds its sink in the worker).
`attach()` flushes the shared sink when each session closes but never closes it,
so one session ending never disrupts the others.
