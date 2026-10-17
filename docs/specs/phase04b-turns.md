# Phase 04b: Turns, latencies and usage

## Goal

For any stored call, VoiceGateway can list its turns with each turn's text, its per-stage latencies (transcription, end of turn, hook, LLM, tools, TTS, end to end) and its usage (LLM input, output and cached tokens, TTS characters and audio seconds, STT audio seconds when reported). It reads them from the spans and session report 04a stored, and leaves unknown values unknown. 04c then serves this through `getCall` and `listCalls`.

## Context

- PRODUCT.md: V1 capability 1 (per-turn latency and usage); principles 2 (unknown stays unknown) and 5 (measured, not claimed).
- ARCHITECTURE.md: Data Model (Turn, Span), Boundaries (a missing value is stored as unknown).
- Decision 0010: usage, not prices: LLM input, output and cached tokens; STT audio seconds; TTS characters and seconds.
- Decision 0014: the span tree and `chat_history.json` as the agent's sources.
- `docs/specs/phase04-call-join.md` (04a): spans stored and linked to calls; this phase interprets them.
- `docs/api/openapi.yaml`, schemas `Turn` (line 1194), `Call.usage` (line 1041), `ChainStage` (line 3580): the shapes 04c will serve. `docs/api/upstream-sources.md`, "Verified distinctions": units and sentinels.
- `dashboard/src/features/call/call-model.ts` `fromSpans`: today's console rule, answer time = `lk.e2e_latency`, never derived from span gaps.

Fields, from LiveKit Agents 1.8.7 (`livekit/agents/telemetry/trace_types.py`, `llm/chat_context.py` `MetricsReport`) and the 1.8.6 capture in `dashboard/src/mocks/capture-session.json`:

| Value | Source | Unit | Unknown when |
| --- | --- | --- | --- |
| Turn identity | `agent_turn` span, `lk.generation_id` | | |
| End to end | `agent_turn` `lk.e2e_latency` | s | absent |
| Transcription delay | `agent_turn` (else the preceding `user_turn`) `lk.transcription_delay` | s | absent, or 0 (speech end not detected) |
| End of turn | `agent_turn` (else `user_turn`) `lk.end_of_turn_delay` | s | absent, or 0 (as above) |
| Hook | `agent_turn` `lk.on_user_turn_completed_delay` | s | absent |
| Reply first token (the console's `ttftMs`) | `lk.response.ttft` of the last `llm_node` starting before the turn's first `tts_node`: the generation the caller first heard. With a tool that is the post-tool generation; an earlier `llm_node` discarded by preemptive generation never counts | s | absent or -1 |
| LLM first token, split | llm1: the last `llm_node` before the turn's first `function_tool`, or the reply's when it used no tool; llm2: the last `llm_node` after the last `function_tool` and before the next `tts_node`, null without a tool | s | absent or -1 |
| Tools | `function_tool` child spans, end minus start, summed | s | no such span: the turn used no tool, so null, not 0 |
| TTS first byte | `tts_node` child `lk.response.ttfb` | s | absent |
| Realtime first token | `realtime_inference` `lk.realtime_model_metrics` `ttft` | s | absent or -1 |
| Interrupted | `agent_turn` `lk.interrupted` | bool | absent |
| LLM tokens | `llm_request` `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`; else `lk.llm_metrics` `prompt_tokens`, `completion_tokens`, `prompt_cached_tokens` | tokens | attribute absent |
| TTS characters, audio | `tts_request` `lk.tts_metrics` `characters_count`, `audio_duration` | chars, s | absent; `audio_duration` 0 |
| STT audio | no 1.8.7 span or `MetricsReport` field carries it | s | always, until a source does |
| Models | `gen_ai.request.model` on `llm_node`, `tts_node`, `user_turn` | | absent |
| User and agent text | `chat_history.json` messages, `role` and `created_at` | | no report, or no message in the turn's window |

A child belongs to the `agent_turn` it descends from by parent span id, through any depth (`agent_turn` > `llm_node` > `llm_request`). A message belongs to a turn by time and order: the assistant message whose `created_at` falls inside the `agent_turn` span is the agent text; the user messages after the previous turn's assistant message, up to this one, are the user text. The user message cannot be matched by the `agent_turn` start, because with preemptive generation the turn starts before the user message is created.

Checked on a real LiveKit Agents 1.8.7 call on the LiveKit Cloud test project (2026-10-17, room `vg-check-130314`, LiveKit Inference): 3 `agent_turn` spans; every assistant message's `created_at` inside its `agent_turn`; in turn 3 the agent began generating at 619.670 s, before the user message (621.552 s), and a second `llm_node` (TTFT 0.342 s) produced the spoken reply, the value `chat_history.json` reports as `llm_node_ttft`. The first `llm_node` (0.473 s) was discarded; its tokens were still billed. Assistant messages in 1.8.7 also carry `e2e_latency`.

## In Scope

- **`internal/turn`**, a pure package: `Build(spans []store.Span, history []byte) []Turn`. It returns one Turn per `agent_turn` span, ordered by start, with the values above in milliseconds (seconds times 1000), every one nullable. Each turn's usage, and the call's usage over every request in it, is a total: the sum of the reported values with a count of the requests that did not report it. The total is null only when none reported it, so a partial sum is never mistaken for a complete one.
- **`chstore`**, a read used by 04c: `CallTurns(ctx, org, project, callID)` loads every span that resolves to the call (its own call id, or its trace's link, as `SpanCalls` does) and the call's session report payload, then calls `turn.Build`. Nothing new is written. Turns are derived at read time, so spans arriving in any order or late change the answer without a rewrite.
- **Sentinels**, exactly as `docs/api/upstream-sources.md` sets them: -1 TTFT is null; 0 transcription and end-of-turn delays are null; 0 TTS `audio_duration` is null. Any other 0 is a measured zero and stays 0.
- A turn with no `agent_turn` span is not invented. A call whose agent sent no spans has no turns (an empty list), not turns built from text alone.

## Out of Scope

- Serving turns or usage over HTTP, the contract changes for cached tokens and TTS audio seconds (`Turn` and `Call.usage` lack both), and the console (04c).
- A stored `turns` table or rollups for `listCalls` aggregates. Decided once 04c measures a read.
- Other frameworks, the SDK, OTLP logs and metrics exports, and recordings.
- Event-loop stalls (`event_loop_blocked`), EOU probabilities and the speech-gap fallback for answer time.

## Constraints

- Decode with the types already in use: `store.Span` attributes as 04a stored them, `encoding/json` for `lk.llm_metrics`, `lk.tts_metrics` and `chat_history.json`. Write no OTLP parsing.
- `internal/turn` does no I/O. `chstore` only loads and calls it.
- Read attribute keys as constants named after the 1.8.7 `ATTR_*` they mirror, and list the version in the package doc.
- Never log span attribute values or transcript text.
- Tests: table-driven unit tests for `internal/turn`, and one ClickHouse testcontainers test for `CallTurns`.

## Acceptance Checks

1. Given the 126 spans of `dashboard/src/mocks/capture-session.json`, converted to `store.Span` (its `startMs`, `durationMs`, `parentId` and `attrs`), when built into turns, then there are 5 turns, and for each turn `ttftMs`, `ttsMs`, `endOfTurnMs`, `transcriptionDelayMs` and `e2eMs` equal the fixture's own `turns` values to within 0.1 ms, nulls included (the greeting turn has no end to end) (measured by `TestBuildMatchesConsoleCapture`).
2. Given the same spans, then the call's input and output tokens equal the fixture's `usage` (2821 and 243), with 0 requests missing them (measured by `TestUsageMatchesConsoleCapture`).
3. Given a turn whose `llm_node` reports `lk.response.ttft` of -1, whose `user_turn` reports `lk.end_of_turn_delay` of 0, and whose `tts_request` reports `audio_duration` of 0, then those three values are null; a `lk.response.ttfb` of 0 stays 0 (measured by `TestSentinelsBecomeNull`).
4. Given a turn with one `function_tool` span between two `llm_node` spans, then llm1 and llm2 are the two TTFTs in start order, the reply first token is llm2's, and tools is the tool span's duration; a turn without one has null tools and null llm2 (measured by `TestToolTurnSplitsLLM`).
5. Given 3 `llm_request` spans of which 1 lacks token attributes and `lk.llm_metrics`, then the call input tokens are the sum of the other 2 with 1 missing; given none reporting, the total is null, not 0 (measured by `TestUsageCountsMissingNotZero`).
6. Given a `chat_history.json` with 2 user and 2 assistant messages, and spans for 2 turns, then each turn carries the user and agent text of its window; a third `agent_turn` with no message in its window has null texts (measured by `TestTextPairsByTime`).
7. Given a call's spans split across 3 exports stored out of order, with one span joined only through its trace link, when `CallTurns` reads the call, then it returns the same turns as `turn.Build` on all the spans at once, and nothing from another call in the same room name (measured by `TestCallTurnsReadsStoredSpans`).
8. Given a call with webhooks only and no spans, then `CallTurns` returns 0 turns and a usage with every total null (measured by `TestCallWithoutSpansHasNoTurns`).
9. Given the spans and session report of the 2026-10-17 real call, committed as `internal/turn/testdata`, then there are 3 turns; turn 3's reply first token and llm1 are 342.1 ms (the spoken generation, equal to its `chat_history.json` `llm_node_ttft` to within 0.1 ms), not the discarded 472.6 ms; its input tokens include both requests (67 + 73); and each turn's user and agent text is the message of its turn, including turn 3, whose user message was created after its `agent_turn` started (measured by `TestRealCallPreemptiveGeneration`).

## Manual Check

On the stack holding the 2026-10-16 1.8.7 call (or one new call on the LiveKit Cloud test project), print `CallTurns` for that call through a test helper. Compare each turn's end-to-end and TTFT with the agent's own log lines for that call, and write the result in docs/progress.md.

## Open Questions

- Turns are derived at read time, not stored. This is my recommendation, because spans arrive in separate exports in any order and late. A stored `turns` table needs a recompute rule for late spans, and two workers can race on it. `listCalls` aggregates over many calls may need rollups later; 04c measures that. Say if you want a stored table now instead.
- STT audio seconds: LiveKit Agents 1.8.7 sends it in no span attribute or `MetricsReport` field, so it stays null for LiveKit agents in V1 unless a source is found. Decision 0010 lists it, so this is a known gap, not a bug.
- The 1.8.6 capture is the parity fixture because it is the only one with console-normalised turns; the 2026-10-17 1.8.7 call is the second fixture, for the rules the 1.8.6 capture does not exercise. Its transcript is the synthetic caller's two scripted questions and the agent's replies, so it holds no personal data.
