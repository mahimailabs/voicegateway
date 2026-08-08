---
title: Dead air
description: What counts as a dead-air event, the default threshold, and where detected events surface.
---
Dead air is silence on a call that outlasts a threshold: neither the caller nor the agent speaking for longer than expected. `attach()` runs a per-session watchdog that polls for it and writes one event per silence stretch.

## What counts as dead air

`DeadAirDetector` polls once a second (`poll_interval_seconds`, default `1.0`) and asks an `activity_probe` callback for the session's last-known activity timestamp. If the gap since that timestamp is at least `threshold_seconds` (default `3.0`), it emits one `DeadAirEvent` and latches so it won't fire again until activity resumes and the gap re-crosses the threshold: one event per silence stretch, not one per poll (`src/voicegateway/middleware/dead_air_detector_middleware.py:17-18,43-124`).

The probe `attach()` supplies is driven by the same four speech-boundary events that feed [turn capture](/guide/turns). While either party is speaking the clock reports now, so **a long utterance cannot trip a dead-air event**. That is a guarantee of the wiring, not a tuning artifact.

An event row carries:

| Field | Meaning |
|---|---|
| `session_id` | The call it happened on. |
| `started_at_ms` | The last-activity timestamp the probe returned, not the poll time. |
| `duration_ms` | How long the silence had run when it crossed threshold. |
| `threshold_used_ms` | The threshold in effect for this detector, in milliseconds. |

Rows land in `dead_air_events` and are read back oldest-first by session, or counted by session or time window.

## Turning it on

On by default:

```python
voicegateway.attach(session, project="my-agent")                 # dead air watched
voicegateway.attach(session, project="my-agent", dead_air=False) # not watched
```

`VOICEGW_DEAD_AIR=0` forces it off fleet-wide and beats the argument.

It is a separate flag from `turns` even though both ride the same speech events, because dead air **polls**: one task per session, once a second, for the life of the call. That is a standing per-session cost, so it is switchable on its own.

<Warning>
LiveKit only. Pipecat accepts `dead_air=` for signature parity but has no equivalent speech-boundary events, so its dead-air view stays empty.
</Warning>

Events can also be pushed directly with `POST /v1/ingest/dead-air`; see the [HTTP API](/api/http-api).

## Tuning the threshold

`dead_air_threshold_seconds` under `projects.<id>.metrics` in `voicegw.yaml`, default `3.0`, per project rather than global. Three seconds is aggressive for an agent with a slow LLM: raise it if normal thinking time is firing events.

The one-second poll interval is a constructor argument with no config key.

## When the watcher doesn't start

The watcher is scheduled with `loop.create_task(...)`, which needs a running asyncio event loop when the session is wired up. Without one (synchronous code, a sync test rig) the auto-start is skipped: nothing crashes, no watcher runs, and the skip is logged once at debug.

## Where you see it

`GET /api/sessions/{id}/dead_air` returns one session's events, oldest first, with the same tenant-scoped 404 as the parent session (`src/voicegateway/server/api/dashboard/sessions.py:225-248`; full shape in the [Dashboard API](/api/dashboard-api)). The dashboard has no UI consumer for that raw list yet. An aggregate count across the filtered window (`dead_air_event_count`) surfaces via `GET /api/metrics` and is shown as a card on the dashboard's Costs page, Conversation tab, alongside the response speed and talk-over rate covered under Turns and transcripts.
