# Phase 04: Join stored events into calls

## Goal

A real LiveKit call that reaches VoiceGateway through its webhooks, the agent's session upload and the agent's spans is stored as one call, with its participants and every span linked to it, even when the room name is reused. Together with phase 03 this meets the plan task "Capture the first real call": stored once, its events correctly correlated, unknown fields left unknown.

The whole join does not fit one session, so the phase is split. **This spec is 04a**: calls, participants and span links. **04b** (next spec) decodes turns, their latencies and usage from the session report and the spans. **04c** serves `listCalls` and `getCall` from storage, which is "Connect the console".

## Context

- PRODUCT.md principles 2 (unknown stays unknown) and 3 (never lose data quietly).
- ARCHITECTURE.md: Data Flow step 3, Data Model (Call, Participant, Span), Boundaries (worker owns every write; the Redis stream is web's only link to it). Postgres holds the call index; ClickHouse holds events and spans.
- Decision 0012: a call is a room and its time window, or a job id; spans join through their trace id; an event that matches no call waits briefly, then is stored as unmatched and counted; a call whose end never arrives is closed by a timeout and marked inferred; SIP legs are participants of kind SIP.
- Decision 0014: what the Agents uploads carry. Decision 0007: ack only after the write.
- `docs/specs/phase03-first-call.md`: the events table and the sink this phase extends.

How the sources identify a room, from LiveKit's own types (livekit/protocol v1.52.1), livekit-agents 1.8.7 (`livekit/agents/telemetry/traces.py`) and the raw traces stored on 2026-10-16:

| Source | Room instance | Job | Other |
| --- | --- | --- | --- |
| Webhook (`WebhookEvent`) | `room.sid`, `room.name`, `room.creation_time`; Egress: `egress_info.room_id` (the room SID); Ingress: `ingress_info.room_name` only | `job` (field 12, `job.id` and `job.room`) on LiveKit Cloud; absent on open-source LiveKit | `id` (field 6, the dedupe key, kept on redelivery), `participant.sid`, `identity`, `kind`, event `created_at` |
| Session report header (`MetricsRecordingHeader`) | `room_id` (the room SID), `room_name`, `room_start_time` | `job_id` | `start_time`, `duration`. The stored 1.8.7 report sends only `roomId`, `startTime` and `jobId`, so a call's start normally comes from its webhooks |
| OTLP spans | `room_id` (the room SID, no prefix) on every span, stamped at span start by `_MetadataSpanProcessor`; `lk.room_sid` also on `job_entrypoint`, `room_connect` and `agent_session` | `job_id` (no prefix) on every span | `trace_id`. The resource's `room_id` and `job_id` are set once per process and go stale for later jobs: never read |

Checked on the raw traces stored on 2026-10-16: all 30 spans of 2 jobs carry `room_id` and `job_id`; `lk.room_sid` is on 3 per trace. The 3 of 126 counted earlier came from the console fixture `capture-session.json`, not from a raw upload.

A room's SID names one room instance, from its start to its end, so it is the room-and-time-window of 0012 without a window to guess. The worker keys a call on the room SID; the job id joins a session report or a span whose room SID is missing; room name plus time window is used only when neither is present (Ingress). Linking by trace id (0012) is the fallback for spans that carry neither id, such as spans from other frameworks or our SDK. This applies 0012; it does not change it.

## In Scope

- **Postgres** (`migrations/postgres`, sqlc): `calls` (id, organisation, project, room name, room SID, job id, started at, ended at, end state `observed | inferred | open`, last activity) with one call per project and room SID; `call_participants` (call, participant SID, identity, kind as LiveKit's `ParticipantInfo.Kind` names it: `standard`, `ingress`, `egress`, `sip`, `agent`, `connector`, `bridge`; proto3 omits the default `standard`, so a participant with no kind is stored as `standard`, the protocol's own value, joined at, left at). Every time column is nullable: a time no source reported stays null.
- **ClickHouse** (`migrations/clickhouse`): a `spans` table (one row per span, decoded from the OTLP trace exports, deduplicated on trace id and span id) and a `trace_calls` table (trace id to call id). `events` gains `call_id`, empty when unmatched.
- **Worker, joining:**
  - A webhook with a room SID upserts that room's call: `room_started` opens it and sets its start; `participant_joined` and `participant_left` upsert the participant; `room_finished` sets the end and `observed`. When `job` is present (LiveKit Cloud), it sets the call's job id. Webhooks are deduplicated on `WebhookEvent.id`.
  - A session report upserts the call for its `room_id`, and sets the job id and, when no webhook did, the start from `room_start_time`.
  - An OTLP trace export is decoded into spans with pdata (`ptraceotlp`). Each span joins its call by its own attributes, in this order: `room_id`, then `job_id`, then `lk.room_sid`, then `lk.job_id`, creating the call if no webhook has. Resource attributes are never used. A span that joins also links its trace in `trace_calls`.
  - Fallback: a span with none of those ids is stored with an empty call id and resolves at read time through `trace_calls` once any span of its trace joins.
  - Each stored event row carries the call id it resolved to.
- **Unmatched, counted:** a webhook or session report that resolves to no call is stored with an empty call id and counted: one row in a ClickHouse `unmatched` table (kind, key, reason), so the count survives restarts and is never counted twice, and a WARN log line with kind and project, never the body. A trace with a span that has no call 15 minutes after the trace's last span, and that no span of the trace has linked, is counted the same way, once. The count means "unlinked at 15 minutes": a span that links the trace later resolves at read time, and its `unmatched` row stays as the record that it was late. The Prometheus counter ARCHITECTURE.md names for unmatched events reads this table once the worker exposes metrics; that endpoint is not in this phase.
- **Egress and Ingress webhooks:** an Egress webhook carries the room SID (`egressInfo.room_id`) and joins by it. An Ingress webhook names only its room (`ingressInfo.room_name`), so it joins the call open in that room at its reported time, the room-name-and-window fallback, and is unmatched when none is.
- **Inferred end:** a call still open with no activity for 2 hours is closed and marked `inferred`. Activity is any reported time from any source joined to the call: a webhook's `created_at`, a span's end, a session report's start plus duration, so a long SIP call whose agent keeps exporting spans stays open. Its end is the latest such time, null if none.
- **Acknowledgement:** an entry is acknowledged only after its event row, its span rows and its call and participant writes all land. A failure in any of them leaves it pending (0007).

## Out of Scope

- Turns, their latencies, tokens and audio seconds (04b). Spans are stored and linked, not interpreted.
- `listCalls`, `getCall`, `getCallStats` from storage, and the console (04c).
- OTLP logs and metrics exports: still stored whole as events, not decoded.
- Recordings and Egress (0015), scraped metrics, SIP trunk reads.
- Merging two calls that turn out to be one, and splitting one that is two. A call is decided once by its room SID.

## Constraints

- OTLP decoding with OpenTelemetry pdata (`go.opentelemetry.io/collector/pdata`, `ptraceotlp.ExportRequest.UnmarshalProto`), as ARCHITECTURE.md and 0009 name; no hand-written OTLP parsing. Webhook and header types from livekit/protocol, as in phase 03.
- pgx v5 with sqlc for the call index; clickhouse-go v2 for spans. Migrations only add.
- Calls are written by the worker only; web never writes them (Boundaries).
- Never log a raw body, a span attribute value or a participant identity; ids, kinds and counts only.
- A call or participant time is set only from a reported value; never from the time the worker processed it.
- Run the real thing in tests (testcontainers-go for Postgres, Redis and ClickHouse). The 15-minute and 2-hour sweeps take the current time as an argument, so tests pass an explicit clock; synctest cannot fake time while a goroutine waits on a container's network.

## Acceptance Checks

1. Given `room_started`, `participant_joined` (agent and standard), `participant_left` and `room_finished` webhooks for one room SID, when the worker stores them, then there is exactly 1 call with that SID, start and end from the webhooks, end state `observed`, 2 participants with their kinds, and all 5 event rows carry its call id (measured by `TestJoinWebhooksIntoCall`).
2. Given two rooms with the same name and different SIDs, when their webhooks are stored interleaved, then there are 2 calls and no event row carries the other room's call id (measured by `TestReusedRoomNameIsTwoCalls`).
3. Given a session report whose header names a room SID that webhooks already opened, then it joins that call and sets its job id; given one whose room SID no webhook named, then 1 new call is created with start from `room_start_time` and end state `open` (measured by `TestSessionReportJoinsCall`).
4. Given LiveKit Agents span exports in which every span carries `room_id` and `job_id` and the root arrives last, when both batches are stored, then every span carries the call id of its own `room_id` without a read through `trace_calls`; a span whose resource names another room still joins by its own attribute; and each span is stored once even when the export is delivered twice (measured by `TestSpansJoinByOwnIDs`).
5. Given a span with no room or job id in a trace where another span joined a call, then it resolves to that call through `trace_calls` and 0 are counted unmatched (measured by `TestSpansJoinThroughTraceID`). Given a trace none of whose spans carries an id, when 15 minutes pass after its last span, then its spans stay stored, the `unmatched` table holds 1 row for that trace (still 1 after a second sweep), and none is dropped (measured by `TestUnlinkedTraceIsCountedNotDropped`).
6. Given an Egress webhook, then it joins the call of its `room_id`; given an Ingress webhook for a room with an open call at its time, then it joins that call; given one for a room with no call, then it is stored with an empty call id, counted in `unmatched`, and no call is created (measured by `TestEgressAndIngressJoinOrStayUnmatched`).
7. Given a call with `room_started` and no further event, when 2 hours pass, then it is closed with end state `inferred` and its end at the last time a source reported for it (null if none did); a call whose spans keep arriving stays open; a call that receives `room_finished` within that time is `observed` (measured by `TestOpenCallClosesAsInferred`).
8. Given a room whose webhooks report no creation time and no session report, then the call's start is null, never the processing time or zero; a participant with no reported join time has a null joined at (measured by `TestUnreportedTimesStayNull`).
9. Given the same webhooks (same `WebhookEvent.id`) delivered twice and the worker restarted between them, then 1 call, 1 row per participant and 1 row per event remain (measured by `TestJoinIsIdempotentAcrossRestart`).
10. Given Postgres unreachable while ClickHouse answers, when entries are read, then none is acknowledged, no event row is left with a call id that has no call, and once Postgres answers every entry is joined (measured by `TestJoinAcksOnlyAfterEveryWrite`).
11. Given `voicegateway migrate` run twice on the phase 03 schema with stored events, then the new tables and the `call_id` column are added, the existing event rows remain, and the second run applies nothing (measured by `TestMigrateTwice`, extended).

## Manual Check

One real call on the LiveKit Cloud test project, with its webhook pointed at the stack and an agent run with `LIVEKIT_OBSERVABILITY_URL` set to it. Afterwards: one `calls` row with the room SID, job id, start, end and `observed`; the caller and the agent in `call_participants`; every stored span of that job resolving to the call; no `unmatched` row for that call's events or traces. Then `make down` and `make up`, and the same rows, with no duplicates.

## Open Questions

- The 15-minute link window and the 2-hour inferred-end timeout are recommendations, sized so a long call's root span (which ends with the job) still links and a crashed room closes the same day. Change either before building if you want different numbers.
