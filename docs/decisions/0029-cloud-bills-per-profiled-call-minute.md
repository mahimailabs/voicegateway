# 0029: Cloud bills per profiled call minute

- Status: accepted
- Date: 2026-10-18

## Context

Decision 0024 sells VoiceGateway Cloud self-serve through Stripe and left the billing unit open. It ruled out per audio minute: VoiceGateway stores telemetry, it does not carry audio. Cloud needs a unit a customer can predict from their own call volume and VoiceGateway can count from its own data, without trusting a number the agent reports.

## Options

1. Per call: simple to count, but a ten second call and a forty minute call cost the same, so the price tracks neither the customer's value nor our storage.
2. Per stored GB: tracks our cost, but a customer cannot predict it before sending data, and it changes with event volume they do not control.
3. Per profiled call minute: tracks call volume the customer already knows, and VoiceGateway counts it from the call records it stores.

## Decision

Option 3. Cloud bills per profiled call minute: the duration of each call VoiceGateway recorded telemetry for, from room start to room end as stored on the call record, summed per organisation per billing period. The count comes from VoiceGateway's own records and is reported to Stripe, as 0024 requires.

- A call whose end was observed is billed from start to end. A call closed as inferred is billed from start to its last activity, a lower bound that never overcharges, and is labelled inferred wherever its minutes are shown. An open call is counted once it closes. A call with no start time is not billed.
- A closed call whose end is unknown, or whose end is before its start, is not billed. It is counted as unmeasured in the billing period that holds its start.
- A call counts in the billing period that holds its end.
- Seconds are summed per organisation per period, then rounded down to whole minutes once.
- Plans carry a monthly minute allowance. Over it, ingest answers with a clear refusal, never a silent drop (0024).
- Prices live in Stripe, not in this repository.

## Consequences

- The count needs a usage query per organisation per billing period, served by the instance API (phase 06e).
- Self-hosted installs are unaffected: nothing is counted or reported.
- Recordings are not a separate billing unit in V1.
