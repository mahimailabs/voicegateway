# Phase 04c: Serve one call from storage

## Goal

The console's call drawer reads a real stored call through web's API: `getCall` returns its room, times, end state, spans, turns and usage from what 04a and 04b stored, in the contract's `Call` shape, with every unknown value null. This is the first half of the plan task "Connect the console"; 04d serves `listCalls`.

## Context

- PRODUCT.md: principles 2 (unknown stays unknown) and 5 (measured, not claimed).
- ARCHITECTURE.md: Boundaries (the dashboard reads only through web's API; web reads Postgres and ClickHouse, never writes ClickHouse).
- `docs/api/openapi.yaml`: operation `getCall` (line 193), schemas `Call` (984), `Span` (1155), `Turn` (1194), `Clock`, `Error` (3839).
- `docs/specs/phase04-call-join.md` (04a) and `docs/specs/phase04b-turns.md` (04b): the stored calls, spans, turns and usage this phase serves.
- #365 (phase 05a) adds `internal/api` on chi with the error shape and a pass-through access gate (#321 has not decided the scheme). This phase uses the same package, file names and conventions; whichever lands second merges the two.

The whole of "Connect the console" does not fit one session. `listCalls` has cursor and snapshot paging, fifteen filters, server-side sort with unknowns last, and range-wide totals; it is 04d. This spec is `getCall` only.

## In Scope

- **`internal/api`**, a chi sub-router at `/api/v1/org/{orgId}/project/{projectId}`, mounted by web, one file per operation (`getcall.go`). It reads Postgres (the call, its participant count) and ClickHouse (`chstore.CallTurns` and the call's spans) and writes nothing.
- **Field mapping**, each from a stored, reported value; anything else is null:

| `Call` field | Source |
| --- | --- |
| `id` | `calls.id` |
| `room` | the room name a webhook or session report gave, else the `lk.pii.room_name` attribute on the call's `job_entrypoint` or `agent_session` span |
| `roomSid` | `calls.room_sid` |
| `startedAt` | `calls.started_at` (webhook or report room start), else the earliest start of a span joined to the call |
| `endedAt` | `calls.ended_at` |
| `durationSeconds` | `endedAt` minus `startedAt` when both are known and `startedAt` came from `calls.started_at`; else null |
| `participantCount` | the call's `call_participants` rows, null when no webhook reported a participant |
| `endState` | `calls.end_state` |
| `endReason` | null: no stored source yet |
| `spans` | the call's spans (own call id or trace link), `startMs` relative to the trace clock, `attrs` as stored; null when none was stored |
| `turns` | 04b's turns; null when the call has no `agent_turn` span |
| `usage` | 04b's call usage; omitted when no request and no usage point reported anything |
| `clocks` | `call` (origin `call_start`) and `trace` (origin `trace_start`), below |
| `tags`, `archived` | `[]` and `false`: the contract requires them, the server stores neither yet, and the console keeps both in the browser (progress.md, mock API part 1) |
| `events`, `recording` | omitted: no stored source |

- **Turn mapping:** `model` the reply's LLM model; `ttftMs` the reply first token; `ttsMs`, `endOfTurnMs`, `transcriptionDelayMs`, `e2eMs`, `interrupted` as built; `inputTokens`, `outputTokens`, `characters` the turn's totals only when none of its requests is missing them, else null; `llmMs`, `sttMs`, `audioSeconds` (STT audio, which has no per-turn source), `eotProbability`, `eotThreshold` null; `userItemAt` and `agentItemAt` omitted.
- **Usage mapping:** `llmInputTokens`, `llmOutputTokens`, `ttsCharacters` the call totals only when no request is missing them, else null; `sttSeconds` the sum of the call's STT usage points, null when none; `note` says where each came from and, for a value left null, how many requests did not report it.
- **Clocks:** `call` has `originAt` = `startedAt`, offset 0, alignment `observed` when `startedAt` came from `calls.started_at`, else `inferred`. `trace` has `originAt` = the earliest span start, alignment `observed` with the offset to `startedAt` when both are known, else `unknown` with a null offset. Spans and turns use `trace`.
- **Errors:** an unknown organisation, project or call, a call of another project, and a call id that is not a UUID are all `404 not_found` with the request id; a storage failure is `500 internal_error`. Never an empty success.

## Out of Scope

- `listCalls`, its filters, paging and totals (04d).
- Contract changes. `llm1`/`llm2`, tool and hook time, cached tokens, TTS audio seconds, the participant list and the job id have no slot in `Call` (`additionalProperties: false`); exposing them is its own decision.
- The access scheme (#321) and the console's switch from the mock to the server (`VITE_MOCK_API`).
- Repointing the contract's `x-source` pointers from the mock fixtures; done when 04d lands and both operations are served.

## Constraints

- chi for routing, as #365; `encoding/json`; the contract's own field names and the `Error` shape from #365's `errors.go`.
- Validate responses in tests against `docs/api/openapi.yaml` with a library that reads OpenAPI 3.1 (the contract is 3.1.1): pb33f `libopenapi-validator`, not a hand-written schema check.
- Never log a span attribute value, transcript text or participant identity.
- Real-thing tests: Postgres and ClickHouse through testcontainers, data stored through the 04a sink and joiner, requests through `httptest` against `Routes()`.

## Acceptance Checks

1. Given the 2026-10-17 call with webhooks (room `vg-full-134152`: its 10 webhooks, 56 spans, session report and metric exports, committed as testdata) stored through the sink, when `getCall` is requested, then the response is 200, validates against the contract's `getCall` 200 response, and has `room` `vg-full-134152`, `roomSid` `RM_35ayuuwAkwVj`, `startedAt` `2026-10-17T17:41:53.336Z`, `endedAt` `2026-10-17T17:42:35Z`, `durationSeconds` 41.664, `participantCount` 2, `endState` `observed`, a `call` clock aligned `observed`, 3 turns whose third has `ttftMs` 323.6 (the spoken generation of two) and `e2eMs` 844.6 (within 0.1), and `usage.sttSeconds` 33.25 (within 0.001) (measured by `TestGetCallServesStoredCall`).
2. Given the 2026-10-17 call only the agent's uploads named (room `vg-check-130314`, no webhook), then `room` is `vg-check-130314` from the span's `lk.pii.room_name`, `startedAt` is `2026-10-17T17:03:15.679406Z`, the earliest span start, `endedAt`, `durationSeconds` and `participantCount` are null, the `call` clock is `inferred`, `usage.sttSeconds` is 33.85, and the response validates (measured by `TestGetCallFromAgentUploadsOnly`).
3. Given a call with webhooks only, then `spans` and `turns` are null, `usage` is omitted, and the response validates (measured by `TestGetCallWithoutAgentData`).
4. Given 3 `llm_request` spans of which 1 reports no tokens, then `usage.llmInputTokens` is null and `usage.note` names 1 request missing it; the turn holding that request has null `inputTokens` (measured by `TestGetCallPartialUsageIsNull`).
5. Given an unknown organisation, an unknown project, an unknown call id, a call id that is not a UUID and a real call id under another project, then each is 404 with `code` `not_found`, a non-empty `requestId`, and a body that validates as `Error` (5 of 5) (measured by `TestGetCallNotFound`).
6. Given ClickHouse unreachable while Postgres answers, then the response is 500 `internal_error`, never a 200 with empty spans (measured by `TestGetCallStorageDown`).

## Manual Check

With the stack up and one real call stored, `curl` its `getCall` and open the console's call drawer against the server with `VITE_MOCK_API=off`; the room, times, turns and usage match the stored call, and every value the server does not hold reads unknown.

## Open Questions

- A call that neither a webhook nor a span named the room of (only a session report arrived) cannot fill the required `room`, so `getCall` answers 404 for it until another source arrives. My recommendation is to keep the contract as it is and accept this edge; making `room` and `startedAt` nullable touches every console view of a call.
