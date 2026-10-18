# Phase 04e: Overview from storage

## Goal

With the mock off, the Overview opens on real stored calls: call counts, channels, calls per day and hour, answer times and one row per agent come from web's `getCallStats`, and a call opens from the Overview. A section whose read web does not serve yet says so, and the rest of the page still renders. With 04c and 04d this closes the plan task "Connect the console": the first real call opens from the Overview and from the Calls list without fixture data.

## Context

- PRODUCT.md: principles 2 (unknown stays unknown) and 5 (measured, not claimed).
- `docs/api/openapi.yaml`: `getCallStats` (line 242) and its schemas `CallStats`, `StageTiming`, `ChainStage`, `ProviderUse`.
- `docs/specs/phase04d-list-calls.md`: `call_summaries` and the `call_list` view, the source of every figure here, with listCalls' channel rule and answer budget.
- `dashboard/src/mocks/call-stats.ts`: what the mock computes for the Overview today, the reference for each field's meaning.
- `dashboard/src/features/overview/overview.tsx`: today the page waits for 8 reads and shows one error state when any fails. Web serves 2 of them (media server and SIP).
- `docs/progress.md`: the plan check of 2026-10-18 (which reads the Overview lacks).

## In Scope

- **`getCallStats`** in `internal/api`, behind the role gate as a Viewer project read, over every servable call of the project that started in `[from, to)`: read from Postgres in one transaction and totalled in Go with the console's own rules. `from` and `to` are required RFC 3339 times with `from` before `to` and at most 400 days apart; anything else is 400. Every count is over calls the `call_list` view marks servable, the same rule listCalls uses, so the Overview and the Calls list count the same calls.
  - `callCount`, and `byChannel` by listCalls' channel rule: `phone`, `web` and `unknown` are counted. `console` and `local` are null, because no stored source tells those channels apart yet.
  - `byDay`: one entry per UTC day of the range, oldest first. A day with no stored call is 0 only when it falls wholly after the project was connected (`projects.created_at`) and before now. A day before the project was connected, or one still to come, has `callCount` null: nothing could have been stored then, so 0 would be a claim.
  - `byHour`: one entry per UTC hour over the last 168 hours of the range, oldest first, each with its call count and the median answer over its calls' measured turns (null when none). The call count follows the `byDay` rule: null for an hour before the project was connected or after now.
  - `answers`: over every measured answer time in `call_summaries.answer_ms`. `turns`, `overBudget` against the interim answer budget listCalls uses, `p50Ms` and `p90Ms` by the console's own rule (`median` in `features/call/call-model.ts`, the upper-middle value; `p90` in `mocks/call-stats.ts`, the value at rank ceil(0.9 n)), and `values`: most recent call first, at most 5,000, with `valuesTruncated`.
  - `agents`: one row per `agent_key`, plus one with key null for calls with no agent. Each row has its call count, measured turns and their median, models, and `calls`: the agent's calls, oldest first, each with its room and median answer, at most the most recent 500, with `callsTruncated`. `name` is null, since no stored source names an agent yet.
  - `complete` is true. The server does not detect ingest gaps yet, so true means every call stored since the project was connected, not that nothing was lost. The description of `complete` in `docs/api/openapi.yaml` says so, and the limit is written in `docs/api/coverage.md`.
- **Not computed yet, and said so.** Turn anatomy (`stages`), the stage chain (`chain`), `providers` and `anatomyTurns` need each turn's stage timings, which the summaries do not store. Issue counts need the Issues engine. An empty list would claim that no stage was reported, which is false.
  - The contract makes `stages`, `chain` and each agent's `stages`, `chain` and `providers` nullable. Each of these, every `issueCounts` field, each agent's `issues`, and `anatomyTurns` (overall and per agent) gets an `x-null-reason` naming this case, "Not computed by this server yet", alongside any reason it already has.
  - 04e returns null for them, for `anatomyTurns`, and for every `issueCounts` field and each agent's `issues`.
  - The mock keeps returning its computed values.
- **Overview** (`dashboard/src/features/overview/`):
  - Each section renders from its own read, with its own loading and error state. A section whose read answers 404 shows "Not served by this server yet". Any other failure, a 500 among them, shows the error. Neither blocks the other sections. The sections are the call stats, the agents, the media server, SIP, egress, ingress, workers and provider probes.
  - Null figures show as unknown. Turn anatomy and the stage chain say "Not computed yet" when their list is null.
  - An agent card's per-call strip opens the call drawer for the block clicked (`/calls/<id>`, the Calls page's own call link), so a call opens from the Overview.

## Out of Scope

- Routes for egress, ingress, workers, cloud agents and provider probes (the layers lane, 05c and 05d).
- Computing turn anatomy, the stage chain, providers and issue counts on the server (a later phase stores per-turn stage timings).
- Detecting ingest gaps for `complete`.
- Embed tokens for the answer-time and turn-anatomy widgets.

## Constraints

- Postgres only, from `call_summaries` and `call_list`. No ClickHouse read and no new worker write.
- The reads run in one `REPEATABLE READ READ ONLY` transaction, so the figures agree.
- The response validates against `docs/api/openapi.yaml` in the tests.
- The console uses its existing components and `QueryState`. No hand-written markup where a component exists.

## Acceptance Checks

1. Given 6 servable calls in a range (3 phone, 2 web, 1 unknown, over 2 UTC days and 2 agents plus 1 with no agent) and 1 unservable call, then `getCallStats` returns `callCount` 6, `byChannel` 3/2/1 with `console` and `local` null, `byDay` with one entry per day, including a day with 0, and an agent row per agent plus the null-key row, whose call counts sum to 6 (measured by `TestCallStatsCounts`).
2. Given those calls' answer times, then `answers.turns` and `overBudget` equal the counts in the test, and `p50Ms` and `p90Ms` equal the console's rule applied to the same values: with turns of 100, 200, 300 and 400 ms, `p50Ms` is 300 and `p90Ms` is 400. Each agent's `answerP50Ms` equals its own by the same rule. With 5,001 measured turns, `values` holds 5,000 and `valuesTruncated` is true (measured by `TestCallStatsAnswers`).
3. Given a 3-hour range with calls in hours 1 and 3, then `byHour` has 3 entries, hour 2 with `callCount` 0 and `answerP50Ms` null. A 10-day range gives exactly 168 entries ending at its last hour. A range ending 2 hours after now gives those 2 hours `callCount` null (measured by `TestCallStatsByHour`).
3a. Given a project connected 2 days into a 4-day range with no calls, then `byDay` has 4 entries: the 2 days before the project was connected have `callCount` null, and the 2 after have 0. A partial bucket (the day or hour the project was connected, today, this hour) shows the count so far, 0 included: connected 6 hours into day 3, that day is still 0 (measured by `TestCallStatsBeforeTheProject`).
4. Given any range, then `stages`, `chain`, `anatomyTurns`, every `issueCounts` field and each agent's `stages`, `chain`, `providers` and `issues` are null, never empty or 0 (measured by `TestCallStatsUnknownsStayUnknown`).
5. Given a missing `from`, a `to` before `from`, or a span over 400 days, then 400 `invalid_request`. A call in another project never counts. A Viewer gets 200, a non-member 403 (measured by `TestCallStatsRange`).
6. Given each response in checks 1 to 5, then it validates against the contract (measured by the response validator in those tests).
7. Given the Overview with stats served and the cloud agents, egress, ingress, workers and provider probes reads answering 404, then the stats sections and the agent cards render with their figures, each of the 5 sections says "Not served by this server yet", as does the Live wire, which draws the provider probes too, and turn anatomy and the chain say "Not computed yet". With the egress read answering 500 instead, the egress section shows the error and the others still render. Clicking a block in an agent's per-call strip navigates to `/calls/<id>` (measured by the Vitest suite `overview.test.tsx`, 4 cases).
8. Given the mock on, then the Overview renders as before, with every section filled (measured by the same suite, 1 case).

## Manual Check

On the Compose stack with one real call stored and `VITE_MOCK_API=off`: open the Overview, see the call counted and its agent card, open the call from the agent's strip, then open it from the Calls list.

## Open Questions

These are my recommendations; change any before building.

- Nullable anatomy, chain and providers in the contract, rather than computing them in 04e. Computing them needs per-turn stage timings stored by the worker and a Go port of the console's waterfall, a phase of its own.
- `complete` true with the gap-detection limit written in its contract description and in coverage, rather than false, which would null every total and empty the Overview.
- `console` and `local` channels null rather than 0.
