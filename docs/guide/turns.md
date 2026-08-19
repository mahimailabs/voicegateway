---
title: Turns and transcripts
description: What VoiceGateway records per caller-agent exchange, how that differs from the call transcript, and where both surface.
---
A **turn** is one caller-agent exchange: the caller speaks, the caller stops, the agent responds. VoiceGateway captures the timing of that exchange in a `turns` table, separate from the call **transcript** (the text of what was said) in `transcript_turns`. The two are captured by different code paths and are not row-aligned.

## What a turn is

A turn's boundary is speech events, not dialogue turns in the conversational sense:

1. The caller starts speaking: `caller_speak_start_ms` is latched.
2. The caller stops speaking: `caller_speak_end_ms` is latched.
3. The agent's first audio frame plays: the turn closes and a row is appended, with `response_speed_ms = max(0, agent_speak_start_ms - caller_speak_end_ms)`. A turn with a tool call still in flight does **not** close here: that first frame is filler ("let me pull that up"), and closing on it would report the wait to the holding line rather than to the answer. The turn closes on the first frame after every tool call goes terminal, whether it completed, failed or was cancelled (`src/voicegateway/middleware/turn_tracker_middleware.py`).
4. The agent's last audio frame play sets `agent_speak_end_ms` on that same row.

If the agent starts responding before the caller's stop event lands, `caller_speak_end_ms` backfills to the agent's start time and `response_speed_ms` is `null` rather than negative. A caller turn that never gets an agent response (session ends first) is still written, with `agent_speak_start_ms` / `agent_speak_end_ms` / `response_speed_ms` all `null`
(`src/voicegateway/middleware/turn_tracker_middleware.py:71-205`).

Turns buffer in memory per session and flush to storage at 25 buffered rows or on session close, whichever comes first (`turn_tracker_middleware.py:18,62-67,137-139,159-205`).

The `turns` table also backs two aggregates: `aggregate_response_speed` (p50/p95/p99 over non-null `response_speed_ms`) and `count_overlap_turns`, a talk-over/barge-in count where the caller started before the prior turn's agent finished (`src/voicegateway/repository/turns_repository.py:90-140`). When turns exist for a session, these feed five columns back onto that session's row: `talk_time_seconds`, `per_minute_cost_usd`, `response_speed_p50_ms`, `response_speed_p95_ms`, `talk_over_rate` (`src/voicegateway/repository/session_repository.py:154-201`).

## Which number answers "what did the caller wait"

`response_speed_ms` does, and it is the only one, because it is measured from when the caller **actually** stopped rather than from when the pipeline noticed.

Those differ by the whole VAD silence window, 0.55s on Silero's default. LiveKit raises the stop only after voice activity has waited that window out, so a speed measured from it is short by the same amount on every turn, always in the flattering direction. It reads as good latency rather than as a bug, and the size of the error moves with each operator's `min_silence_duration`, so two deployments could not compare numbers.

The correction comes from `EOUMetrics.end_of_utterance_delay`, which LiveKit measures from the caller's real stop. Subtracting it lands back on the true anchor and tracks any VAD configuration, where adding a constant would not.

<Note>
There is deliberately **no second, uncorrected column**. When a turn carries no end-of-utterance measurement, `response_speed_ms` is `null` rather than falling back to the uncorrected figure. A number that is systematically half a second optimistic is worse than no number, because somebody tuning toward a target answer time would tune against it without knowing. Absent says "not measured"; the uncorrected value would say "fast".
</Note>

Turn boundaries come from `user_state_changed` / `agent_state_changed`, which are the events `livekit-agents` in the supported range actually emits; the four discrete `*_started_speaking` events stay bound for other frameworks and cost nothing where they never fire (`src/voicegateway/inference/session/attach.py`).

## How the transcript differs

The transcript is a separate capture: at session close, [`attach()`](/guide/attach) (default `transcript=True`) reads the LiveKit session's own conversation history and writes each user/agent utterance as a row in `transcript_turns`, keyed by `session_id` and an ordinal `seq` (not `turn_index`). A repeat capture replaces the prior rows for that session rather than duplicating them. This is LiveKit-only today; on Pipecat the flag is accepted but does nothing. Disable it per call with `attach(transcript=False)`, or fleet-wide with `VOICEGW_TRANSCRIPTS=0` (`src/voicegateway/inference/session/attach.py:391-394,430-463,518-535`, `src/voicegateway/repository/transcript_turns_repository.py`).

Because a `TurnRow` measures speech timing and a transcript row carries text from the framework's own history, nothing joins them 1:1: a session can have a transcript with no turns, or turns with no transcript, depending on what's wired up.

## Turning it on

On by default, like the transcript:

```python
voicegateway.attach(session, project="my-agent")            # turns captured
voicegateway.attach(session, project="my-agent", turns=False)  # not captured
```

`VOICEGW_TURNS=0` forces it off fleet-wide and beats the argument, the same shape as
`VOICEGW_TRANSCRIPTS`. It defaults on because a turn row is four timestamps and an index:
no utterance, no prompt, no tool payload, so it is not the disclosure that
[`snapshots`](/cli/replay) is.

<Warning>
LiveKit only. Pipecat accepts `turns=` for signature parity but has no equivalent
speech-boundary events, so no rows are written there and `e2e_ms` stays null.
</Warning>

### Two knobs, per project

Both live under `projects.<id>.metrics` in `voicegw.yaml`, not at the top level:

| Key | Default | Effect |
|---|---|---|
| `turn_buffer_flush_size` | `25` | Rows buffered before the tracker writes |
| `talk_over_min_overlap_ms` | `100` | Minimum caller/agent overlap counted as a talk-over |

`talk_over_min_overlap_ms` changes a published number rather than just a threshold: the
query used to count any overlap at all, so talk-over rates measured before and after are
not comparable.

Feeding turns from outside a LiveKit session is possible too. `POST /v1/ingest/turns`
takes a batch directly; see the [HTTP API](/api/http-api).

## Where you see it

`GET /api/sessions/{id}/turns` returns the ordered rows for one call; `GET /api/sessions/{id}/transcript` returns the ordered dialogue (empty list, not a 404, when nothing was captured). Both are covered in the [Dashboard API](/api/dashboard-api) and carry the same tenant-scoped 404 as the parent session (`src/voicegateway/server/api/dashboard/sessions.py:175-222`). The dashboard renders the transcript in a call's detail panel today; the raw turns endpoint has no UI consumer yet. The five session-aggregate columns, when populated, roll up into `GET /api/metrics` and appear as cards on the dashboard's Costs page, Conversation tab.
