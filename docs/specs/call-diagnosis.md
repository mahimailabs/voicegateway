# Call diagnosis

Plan task: Finish call diagnosis (Development, week 2).

## Audit and scope

Stored-call APIs, transcript pairing, preemptive-generation TTFT, partial-usage accounting and
project isolation already exist (phases 04b, 04c and 04d). Preserve them and their real-capture tests.

The console still pairs speaking spans by array index, walks descendants in export order, hides
span-only waterfalls without speech events, and describes every model error as discarded work.
The call drawer does not display call usage. Cached tokens and TTS audio totals are computed by
Go but omitted by the HTTP projection.

Complete those paths with source span identity, order-independent ancestry, the existing shadcn
Table for usage, additive nullable usage fields, and trace-clock stage timings. Independent
network probes are not parts of a call's measured latency. The agent's E2E metric does not prove
carrier or handset playback delay. Keep unavailable values unknown and errors unattributed
unless the source supports a cause. Recordings, alert delivery and capacity testing stay in their
own plan tasks.

## Acceptance

1. Shuffled exports and a reply with no speech do not attach another turn's speech or tools.
2. A span-only call shows reported LLM/TTS timings without requiring network probes or inventing
   TTFT from a request duration. Independent clocks are not mixed.
3. A model error points to its affected turn without claiming cancellation caused it.
4. The drawer displays reported call usage, distinguishing zero, absent and partial totals.
   Cached input is explicitly included in input tokens. API schema and generated types agree.
5. Existing real-capture HTTP/storage tests and console regressions pass, plus focused tests for
   these corrections. Browser checks cover light/dark and narrow layouts.

## Source evidence

LiveKit session trace documentation read on 2026-10-19:
https://docs.livekit.io/reference/agents/session-traces/

STT runs continuously; transcription delay belongs to user_turn. agent_turn may begin before
user_turn ends. agent_speaking represents a speech run, and function_tool is a turn descendant.
The existing turn builder pins attribute provenance to LiveKit Agents 1.8.7. No new SDK or
hand-written telemetry protocol is introduced.
