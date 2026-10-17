# Phase 04d: List calls from storage

## Goal

The console's Calls page reads real stored calls through web's API: `listCalls` returns a project's calls a page at a time, filtered, sorted and totalled on the server, in the contract's `CallPage` shape, with every unknown value null. With 04c this completes the plan task "Connect the console" for calls.

## Context

- PRODUCT.md: principles 2 (unknown stays unknown) and 5 (measured, not claimed).
- ARCHITECTURE.md: Boundaries (worker owns every write; web reads only). Data Flow step 6 (the worker settles calls on a timer).
- `docs/api/openapi.yaml`: operation `listCalls` (line 16), schemas `CallPage`, `Page`, `CallTotals`, `CallSummary` (2916 to 3219), `Error`.
- `docs/specs/phase04c-get-call.md`: the `/api/v1` router, its gate, and the room and start fallbacks, which this list must agree with.
- The 04b spec's open question: turns are built at read time, and `listCalls` may need rollups. It does: a range filter or sort on answer time or turn count reads every call in the range, so per-call figures must be stored, not rebuilt per request.

## In Scope

- **Call summaries, kept by the worker.** A Postgres `call_summaries` row per call (call id, turn count, answer times in turn order, their median, models most used first, pipeline, agent key, computed at), built with `turn.Build` from the same stored spans 04c serves.
  - The join marks a call's summary stale whenever it links a span, a session report or a usage point to it.
  - A worker sweep rebuilds stale summaries every 10 s, oldest first, at most 200 per sweep. It clears the mark only if nothing joined after the rebuild began, so a late span is never lost to a race.
  - A call with no spans has a summary of unknowns.
- **Columns the list needs, written by the worker:**
  - `calls.first_seen_at`: the earliest reported time of anything joined to the call (a webhook's `created_at`, a span's start, a session report's start), the same value 04c falls back to for `startedAt`.
  - `calls.room_name`: filled from the agent's `lk.pii.room_name` span attribute when no webhook named the room.
  - `calls.created_at`: when the row was written, used only for the snapshot.
  - 04c's `getCall` reads the same columns, so a list row and its drawer name the same room and start. `first_seen_at` widens 04c's `startedAt` fallback: the earliest reported time of any joined source, not only the earliest span. The turn figures can differ for up to the summary lag (about 10 s, longer under a burst, since a sweep rebuilds at most 200), because the drawer builds turns at once and the list reads the stored summary.
- **`listCalls`** in `internal/api` (`listcalls.go`), one Postgres query plus one totals query, behind the 04c gate:
  - **Paging:** `limit` (1 to 100, default 25), and an opaque `cursor` that carries the last row's sort key and id plus the snapshot, the filters, sort and order it was issued for.
  - **Snapshot:** the time of the first page. It fixes which calls belong to the list (rows created after it are left out of every later page), not their values: a call's end, turns or answer times can change between pages, and it keeps its place by `id` while its sort key stays put. The console de-duplicates rows by `id`. Each page's rows and totals are read in one `REPEATABLE READ READ ONLY` transaction. The first page's totals travel inside the snapshot, so every later page reports the same totals as the first.
  - **Cursor misuse:** a cursor sent with different filters, sort or order, or without its `snapshot`, is `400 invalid_request`. So is any value outside its schema.
  - **Sort:** `startedAt`, `durationSeconds`, `turnCount`, `answerMs` (the median) and `issues`, `asc` or `desc`. Unknowns sort after known values in both orders, and ties break by `id`. `startedAt` here is `calls.started_at`, else `first_seen_at`, as in 04c.
  - **Filters served from storage:** `from`, `to`, `room`, `answerMsMin`/`answerMsMax`, `durationSecondsMin`/`durationSecondsMax`, `turnsMin`/`turnsMax` and `unknown`, each as the contract states. A call whose value is unknown never matches a bound.
  - **Filters with no stored source yet:** they keep the contract's meaning, so none silently does nothing.
    - `issues` matches nothing, since every call's issues are unknown, and `sort=issues` orders by `id`.
    - `tag` matches nothing.
    - `archived=true` matches nothing and `archived=false` matches every call; the server stores no archive, and the console keeps archive and tags in the browser.
    - `recorded=true` matches nothing and `recorded=false` matches every call; the server stores no recording yet.
  - **`channel`:**
    - `phone` when a SIP participant joined.
    - `web` when webhooks reported participants and none is SIP.
    - `unknown` when no webhook reported a participant.
    - `console` and `local` are never inferred, since nothing stored says so.
  - **Calls left out:** a call whose room is unknown or whose start is unknown, after the fallbacks, is left out of the items and the totals. This is one predicate, shared with `getCall`, which answers 404 for exactly these calls, so no row opens a missing call.
- **`CallSummary`:** each field from the call and its summary.
  - `endState` is the call's.
  - `durationSeconds` comes only from an observed start, as in 04c.
  - `participantCount` is null without webhooks.
  - `answerMs` holds the turns' `e2eMs` in turn order, with unmeasured turns left out; it is null when no turn measured one.
  - `issues` is `{high: null, medium: null, titles: []}`, `tags` is `[]`, and `archived` and `recorded` are `false`, as the contract requires.
  - `pipeline`:
    - `cascaded` when the spans hold `llm_node` and `tts_node`.
    - `realtime` when they hold `realtime_inference`.
    - `text` when they hold `llm_node` and no `tts_node`.
    - `unknown` otherwise.
  - `agentKey`: `lk.agent_name` on the call's spans, the value most spans carry; else null. The agent participant's registered name would come first, but no webhook field VoiceGateway stores carries it yet.
- **`CallTotals`,** over every call matching the range and filters as of the snapshot:
  - `callCount` and `byChannel`.
  - `answers`: the turns with a measured answer, how many exceed `budgetMs`, their median, and `valuesMs` (at most 1000 values, oldest calls dropped first, with `valuesTruncated`).
  - `budgetMs` is 1500, the console's own default (`dashboard/src/lib/thresholds.ts`), unless `VOICEGATEWAY_ANSWER_BUDGET_MS` sets it. The variable is interim, until thresholds are stored per project.
  - The `issues` counts and `recorded` are null.

## Out of Scope

- Issues, tags, archive and recordings on the server. Their filters behave as stated above until each has a stored source.
- The console's switch from the mock to the server, which waits on the sign-in decision (0027) to send a credential.
- Repointing the contract's `x-source` pointers from the mock fixtures to the server: a contract change of its own after this phase.
- A Prometheus metric for summary lag.

## Constraints

- pgx v5 with sqlc for every query. Migrations only add. chi and the 04c router, gate and `Error` shape.
- The list and totals read Postgres only: no ClickHouse on a list request.
- The summary sweep takes the current time as an argument, so tests pass a clock.
- Validate every response in tests against `docs/api/openapi.yaml` with libopenapi-validator, as 04c does.
- Never log a filter value that could carry a room name, a transcript or a token.

## Acceptance Checks

1. Given the two 2026-10-17 calls stored through the sink, when the summary sweep runs, then the webhook call's summary has 3 turns, `answerMs` `[956.5, 844.6]` (within 0.1, the greeting unmeasured), `pipeline` `cascaded`, and models `openai/gpt-4.1-mini` first. The agent-only call has 3 turns and `first_seen_at` 2026-10-17T17:03:15.679406Z (measured by `TestSummaryFromStoredCalls`).
2. Given a summary built, when a further span joins that call, then its summary is marked stale and the next sweep rebuilds it with the new turn. A span joined while a rebuild runs leaves it stale for the following sweep (measured by `TestSummaryRebuildsAfterLateSpan`).
3. Given 60 calls in one project, when they are read with `limit` 25 by following `nextCursor`, then 3 pages hold all 60 exactly once in `startedAt desc` order, the last page's `nextCursor` is null, and every response validates (measured by `TestListCallsPagesOnce`).
4. Given a first page, when 5 calls are stored and the second page is read with the first page's cursor and snapshot, then none of the 5 appears and the totals equal the first page's. When a call on the second page gets a new turn between the two reads, it still appears exactly once across the pages with its new values (measured by `TestListCallsSnapshotHolds`).
5. Given calls with known and unknown answer medians, when sorted by `answerMs` `asc` and `desc`, then the unknowns come last both times, ties are ordered by `id`, and `answerMsMin` excludes every unknown (measured by `TestListCallsSortsUnknownLast`).
6. Given a cursor, when it is sent with a changed filter, a changed order, or no `snapshot`, then each is 400 `invalid_request` and validates as `Error` (3 of 3). A `limit` of 0 or 101 is 400 too (measured by `TestListCallsRejectsMisusedCursor`).
7. Given calls with a SIP participant, with only standard participants and with no webhooks, then their `channel` is `phone`, `web` and `unknown`, and `totals.byChannel` counts 1 of each. `issues=high`, `tag=x`, `archived=true` and `recorded=true` each return 0 items with `callCount` 0. `archived=false` returns all 3 (measured by `TestListCallsChannelsAndUnstoredFilters`).
8. Given 4 calls with answer times 1000, 1400, 1600 and 2000 ms and one call with none, then `totals.answers.turns` is 4, `overBudget` 2 at `budgetMs` 1500, `p50Ms` 1600 (the console's median, the upper middle value, `dashboard/src/mocks/calls-query.ts`), and the unmeasured call adds nothing (measured by `TestListCallsTotals`).
9. Given three calls, one with no room, one with no start and one with neither (after the fallbacks), then all three are left out of the items and out of `callCount`, and `getCall` answers 404 for each, through the same predicate (measured by `TestListCallsLeavesOutUnservableCalls`).
10. Given `voicegateway migrate` run twice on the 04c schema with stored calls, then the new columns and `call_summaries` are added, existing calls remain, and the second run applies nothing (measured by `TestMigrateTwice`, extended).

## Manual Check

On the stack holding the two real calls (after the worker has rebuilt their summaries), `curl` `listCalls` with the token: both calls appear, newest first, with the turn counts, answer times and channels of checks 1 and 7, and each row's `id` opens the same call through `getCall`.

## Open Questions

- Summaries are rebuilt by a sweep every 10 s, at most 200 a sweep, so a new turn shows in the list about 10 s after its span arrives, longer under a burst. The drawer, read through `getCall`, is current at once. My recommendation is to accept that lag for V1 rather than rebuild inside the ack path, which would hold up ingest on ClickHouse reads.
