---
title: Dead air
description: What counts as a dead-air event, the default threshold, and where detected events surface.
---
Dead air is silence on a call that outlasts a threshold: no activity from the caller or the agent, as the wired probe reports it, for longer than expected. VoiceGateway's `DeadAirDetector` is a per-session watchdog that polls for it and fires an event when it crosses that threshold, written only if a callback is wired to catch it.

## What counts as dead air

`DeadAirDetector` polls once a second (`poll_interval_seconds`, default `1.0`) and asks an `activity_probe` callback for the session's last-known activity timestamp. If the gap since that timestamp is at least `threshold_seconds` (default `3.0`), it emits one `DeadAirEvent` and latches so it won't fire again until activity resumes and the gap re-crosses the threshold: one event per silence stretch, not one per poll (`src/voicegateway/middleware/dead_air_detector_middleware.py:17-18,43-124`).

What counts as "activity" is entirely up to whatever supplies the probe: the detector itself has no notion of speech, VAD, or audio frames.

An event row carries:

| Field | Meaning |
|---|---|
| `session_id` | The call it happened on. |
| `started_at_ms` | The last-activity timestamp the probe returned, not the poll time. |
| `duration_ms` | How long the silence had run when it crossed threshold. |
| `threshold_used_ms` | The threshold in effect for this detector, in milliseconds. |

Rows land in `dead_air_events` (`session_id`, `started_at_ms`, `duration_ms`, `threshold_used_ms`, `created_at`, `tenant_id`) via `create_event`; `list_events_by_session` reads them oldest-first and `count_events_by_filter` counts by session or time window (`src/voicegateway/models/dead_air_event_model.py`, `src/voicegateway/repository/dead_air_repository.py`). Without a callback wired, a fired event is dropped and only logged at debug (`dead_air_detector_middleware.py:35-40`).

## Is the threshold configurable?

In code, yes: `threshold_seconds` and `poll_interval_seconds` are constructor arguments on `DeadAirDetector` (both must be `> 0`). In `voicegw.yaml`, there's a `dead_air_threshold_seconds` key under `projects.<id>.metrics` that parses and validates (default `3.0`, same as the code default). Nothing in the codebase reads it back to configure a running detector, though, so setting it in YAML today has no effect (`src/voicegateway/schemas/config_schema.py:43-47`, `src/voicegateway/core/config.py:56-61,236-244`).

## When the detector doesn't start

Starting a session's watcher is scheduled with `loop.create_task(...)`, which requires a running asyncio event loop at the moment the session is wired up. When there isn't one (synchronous code, a sync test rig), the auto-start is skipped outright: nothing crashes, but no watcher runs for that session, and the skip is logged once at debug: *"no running event loop, skipping DeadAirDetector auto-start. Call detector.start(sid) explicitly."* The caller has to start it by hand in that case (`src/voicegateway/inference/session/attach.py:169-187`).

Binding a live session to `DeadAirDetector` at all is a lower-level integration point than [`attach()`](/guide/attach)/[`guard()`](/guide/guard): `attach()`'s own metric path never touches `dead_air_events`, so dead-air capture is opt-in, not automatic.

## Where you see it

`GET /api/sessions/{id}/dead_air` returns one session's events, oldest first, with the same tenant-scoped 404 as the parent session (`src/voicegateway/server/api/dashboard/sessions.py:225-248`; full shape in the [Dashboard API](/api/dashboard-api)). The dashboard has no UI consumer for that raw list yet. An aggregate count across the filtered window (`dead_air_event_count`) surfaces via `GET /api/metrics` and is shown as a card on the dashboard's Costs page, Conversation tab, alongside the response speed and talk-over rate covered under Turns and transcripts.
