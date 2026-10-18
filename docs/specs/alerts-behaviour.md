# Alerts: behaviour of the seven rules

## Goal

Agree what each of the seven alert rules does once the worker sends them: when a rule fires, how events are grouped and deduplicated, when it recovers, how delivery is retried, and what the console shows when delivery fails. This is the definition step from `v1-connect-plan.md` ("Define alert behaviour"); it does not claim delivery is built. The [worked example](#worked-example-slow-answer-to-webhook) follows one incident through that contract.

## Context

- Decision 0020: a rule is one of the console's checks plus where to send it; channels Slack, Microsoft Teams, Discord, PagerDuty, Opsgenie, email, webhook. Thresholds in Settings drive the conditions.
- Decision 0012: call identity (room plus time window, or job id); webhooks deduplicated on event id.
- PRODUCT.md principles 2 (unknown stays unknown) and 3 (never lose data quietly).
- `dashboard/src/features/alerts/alerts-model.ts`: the seven rules as the console evaluates them today. `docs/api/openapi.yaml`: `AlertEvents`, `AlertSetup`, `AlertRuleSetup`.
- Phase 3 ingest (#338 to #340): webhooks and agent uploads stored as `events` in ClickHouse.

Lines marked **Recommended** were proposals; the maintainer accepted them all on 2026-10-16. Everything else restates what the console or a decision already does.

## Terms

- **Occurrence**: one thing a rule matched, such as one slow call or one failed egress. This is what the Alerts page lists today as an event.
- **Incident**: occurrences of one rule on one subject, grouped. Notifications are sent per incident, never per occurrence.
- **Subject**: what an incident is about. The project for call-level rules, the agent for agent rules, the trunk, egress or ingress for the rest.
- **Firing / resolved**: an incident's two states. A resolved incident that fires again opens a new incident.

## The seven rules

| Rule | Fires when (occurrence) | Source in V1 | Subject (grouping key) | Occurrence id (dedupe) | Resolves when |
| --- | --- | --- | --- | --- | --- |
| `slow-answer` (medium) | A finished call has at least one turn with answer time over `thresholds.answerMs` (default 1500 ms) | Turn latency from agent spans or the session report | project + agent | call id | **Recommended:** no qualifying call in the last 30 minutes of calls, with at least 5 calls seen in that window. Fewer than 5 calls: stay firing, and say so. |
| `cut-off` (medium) | A finished call has at least one turn the caller interrupted | `interrupted` on turns | project + agent | call id | Same as `slow-answer` |
| `job-failed` (high) | LiveKit reports an agent job as `JS_FAILED` | `ListDispatch`, polled for live rooms, on LiveKit Cloud only. On open-source LiveKit 1.13.3 no source carries it: no job webhook, `ListDispatch` stays `JS_RUNNING` until the room closes, a crashed job uploads nothing, and an entrypoint exception ends as `JS_SUCCESS`. There it shows "Not evaluated: no source" | agent | job id | **Recommended:** the next job for the same agent succeeds |
| `slow-join` (medium) | An agent joins more than `thresholds.joinMs` (default 2000 ms) after dispatch | Dispatch time from `ListDispatch` `state.created_at` (nanoseconds), polled while the room is live; join time from the `participant_joined` webhook for identity `agent-<job id>` | agent | job id | **Recommended:** 3 jobs in a row join within the threshold |
| `plain-phone` (medium) | A SIP trunk's media encryption is disabled (`SIP_MEDIA_ENCRYPT_DISABLE`, the proto default) | SIP trunk configuration, read from the LiveKit API (standing condition, not an event) | trunk id | trunk id plus the configuration seen | The trunk's encryption is `allow` or `require` (as `sip-model.ts` clears it), or the trunk is deleted |
| `recording-lost` (high) | An egress ends with status `EGRESS_FAILED` or `EGRESS_ABORTED` (no recording), or `EGRESS_LIMIT_REACHED` (recording saved but cut off at the time limit, per LiveKit's quotas docs; the occurrence says "cut short", not "lost"). These are every ending status in protocol v1.52.1 `EgressStatus` other than `EGRESS_COMPLETE` | `egress_ended` webhook | egress id | webhook event id | Never auto-resolves: the recording is gone. **Recommended:** a person marks it resolved, as on the Issues page |
| `ingress-error` (high) | An ingress ends with `state.status = ENDPOINT_ERROR` | `ingress_ended` webhook (protocol v1.52.1 has only `ingress_started` and `ingress_ended`). Unverified that `ingress_ended` carries `ENDPOINT_ERROR`; check against a real ingress before building | ingress id | webhook event id | The next `ingress_started` for that ingress, or a `ListIngress` read showing a non-error state |

Every webhook named here is one protocol v1.52.1 defines (`webhook/consts.go`). That list also has `agent_job_started` and `agent_job_ended`, which open-source LiveKit 1.13.3 did not send in the pair review's test. If LiveKit Cloud sends them, they are a better `job-failed` source than polling; check before building.

A rule whose source is not connected for a project, such as no agent uploads or no LiveKit API access for SIP, is **not evaluated**. The console shows "Not evaluated: no source". It never shows "0 occurrences" (principle 2).

## Evaluation

- The worker evaluates rules. web and the console only read their results (CLAUDE.md Boundaries).
- Event rules (`slow-answer`, `cut-off`, `job-failed`, `slow-join`, `recording-lost`, `ingress-error`) are evaluated when the data they need is stored. For call-level rules that means once the call ends, observed or inferred (0012).
- **Recommended:** `plain-phone` is re-read every 15 minutes, with the same scrape as the SIP page.
- Thresholds are read at evaluation time. Changing one does not re-evaluate stored occurrences.
- A disabled rule (`AlertRuleSetup.on = false`) is still evaluated, so the Alerts page keeps its history, but it sends nothing.

## Grouping and deduplication

- An occurrence id is stored once per rule. A redelivered webhook or a re-stored call matches the existing occurrence and sends nothing (0012).
- The first occurrence of a subject with no open incident opens an incident and sends a **firing** notification.
- Later occurrences join the open incident. **Recommended:** at most one reminder per open incident every 4 hours, carrying the count since the last message. High-severity rules also send a reminder when the count doubles.
- **Recommended:** if an incident resolves and a new one fires on the same subject within 10 minutes, it is reopened instead (flap guard). No second firing message is sent.

## Recovery

- When the resolve condition in the table holds, the incident is resolved and a **resolved** notification goes to the same channels.
- `recording-lost` is resolved only by a person, and then no resolved notification is sent.
- An incident whose data source stops reporting stays firing and is marked "source silent since <time>". It is never resolved by the absence of data (principle 2).

## Delivery

- One delivery per incident change (firing, reminder, resolved) per channel. Each delivery has our own idempotency key of incident id, change and channel, so a change confirmed delivered is never sent again. Delivery is at least once: a retry after a timeout can post twice when the channel has no deduplication of its own. Webhook deliveries carry the key in a header so the receiver can drop the repeat.
- PagerDuty (`dedup_key`) and Opsgenie (`alias`) get the incident id, the same for every change, so the resolve closes the page the firing opened.
- **Recommended:** retry with exponential backoff (30 s, 1 min, 2 min, then doubling up to 30 min) for 24 hours. Retry 5xx, timeouts and 429 (honouring `Retry-After`). Do not retry other 4xx, which mark the channel broken.
- Deliveries are durable: they survive a worker restart, like ingest entries.
- A channel's secrets (webhook URLs, tokens) are stored server-side and never returned to the browser (`AlertRuleSetup.to` carries kinds only).

## Visible failure state

- Each channel under Settings, Integrations shows its last delivery: delivered, retrying (next attempt at <time>), or failed with the provider's status and message, minus any secret.
- A rule whose last delivery failed on any channel shows "Not delivered to <channel>" on the Alerts page and in its history entry.
- After 24 hours of failed retries the delivery is marked **failed**, the incident keeps its state, and the failure is listed on the Issues page so it is not lost quietly (principle 3).
- **Recommended:** a "Send test" button per channel, recorded like any other delivery.

## History

- The Alerts page history lists incidents (opened, reminders, resolved) and their deliveries, newest first, beside the occurrences it shows today.
- **Recommended:** incidents and deliveries follow the project's trace retention, and default to 30 days when none is set.

## Worked example: slow-answer to webhook

**Captured input, illustrative delivery.** [capture-cloud.json](../../dashboard/src/mocks/capture-cloud.json) records room `vg-capture-1791864362`, agent name `vg-capture` (`lk.agent_name`), and `turns[1].e2eMs = 2304.3`. The call ended at `2026-10-13T04:07:33.383500Z`. The [preview adapter](../../dashboard/src/mocks/fixtures.ts) uses the room as its call id because this capture predates call identity. That preview id is the occurrence id below, not a prescription for production call ids. The greeting's unknown answer time is not zero and does not trigger the rule.

The following is an acceptance scenario, not a record of notifications sent. Assume this captured call is stored in example project `example-project`, the rule is enabled with the default 1500 ms threshold, and webhook channel `example-webhook` is configured. `T` is the time evaluation and the first delivery start after storage; it is not inferred from a capture clock.

1. **Trigger and group.** 2304.3 ms exceeds 1500 ms, opening example incident `example-incident` for subject `example-project` + `vg-capture`. Other slow turns in this call still count as one occurrence. A different slow call for the same subject joins this incident. Re-storing the original call (including after a redelivered webhook) matches the stored occurrence and sends nothing. While still firing, reminders are limited to one every 4 hours and carry the count since the last message. This medium-severity rule has no count-doubling reminder.
2. **Send.** Create one durable firing delivery for this incident and channel. Its message identifies the rule, subject, incident, occurrence, measured 2304.3 ms versus the 1500 ms threshold, and a link to inspect the call. Carry an idempotency key derived from incident id, this firing change and channel in a header. For illustration only: `Idempotency-Key: example-incident/firing-1/example-webhook`. The header name, serialization and payload schema are not API decisions here. Retries reuse the same key; a reminder or resolved change gets its own key.
3. **Retry.** With immediate responses, the attempts are:

   | Attempt | Time | Response | Recorded result |
   | --- | --- | --- | --- |
   | 1 | T | 500 | Retrying; next attempt T + 30 s |
   | 2 | T + 30 s | 500 | Retrying; next attempt T + 90 s (another 1 min) |
   | 3 | T + 90 s | 200 | Delivered; no further retry |

   This scenario records three attempts and one successful delivery. If attempt 3 also failed, the next delay would be 2 min, then double up to 30 min. A worker restart preserves the delivery and retry state. A timeout can mean the receiver accepted the message without returning a response: at-least-once delivery still needs receiver deduplication, so one successful attempt does not prove only one remote side effect.
4. **Show failure.** In an alternative outcome, the same firing change receives 400. Do not retry: mark the channel broken. Settings, Integrations shows the broken channel and its last delivery status (400), with a sanitized provider message; Alerts and its history show "Not delivered to example-webhook". If retryable failures instead continue for 24 hours, mark the delivery failed and list it on Issues. Delivery failure does not resolve the incident. Webhook URLs and credentials never appear in this evidence or in browser responses.
5. **Recover.** In a hypothetical later 30-minute window for this subject, at least five calls are observed and none qualifies as slow. Resolve the incident and send a resolved delivery to the same channel, using a new change key. This recovery window is a scenario condition, not five invented captured calls. Fewer than five calls keep it firing and explain why; a source that stops reporting keeps it firing with "source silent since <time>". If a slow call arrives within 10 minutes of resolution, reopen the same incident and suppress a second firing message under the flap guard.

## Out of Scope

- Building the evaluator, channels or delivery. That is week-2 work in `v1-connect-plan.md`.
- New rules beyond the seven, call scoring, per-rule schedules or quiet hours, escalation policies.
- Changes to `docs/api/openapi.yaml`. The incident and delivery shapes are added to the contract when delivery is built.

## Acceptance Checks

For the spec PR: the maintainer accepts or changes each **Recommended** line, and the table is checked against the console's current rules.

1. Given `alerts-model.ts`, when its seven rules are compared with the table, then ids, severities, conditions and thresholds match, except `recording-lost`, widened by decision (measured by review of this PR).
2. Given each **Recommended** line, then it is accepted or replaced by the maintainer before delivery is built (measured by the PR review thread; done 2026-10-16, see Decisions).

When delivery is built, these become tests:

3. Given the same webhook stored twice, then one occurrence and at most one firing notification exist.
4. Given 10 slow calls in an hour on one agent, then one incident and one firing notification exist, plus reminders on the documented schedule.
5. Given a channel answering 500 three times then 200, then one message is delivered, with 3 failed attempts recorded.
6. Given a channel answering 400, then no retry is made, and the channel and rule show the failure.
7. Given a source that stops reporting, then its open incident stays firing and is marked silent, never resolved.

## Decisions (maintainer, 2026-10-16)

- Every **Recommended** line is accepted, including: low-traffic call-level rules stay firing, and a disabled rule still creates incidents but sends nothing.
- `job-failed` on open-source LiveKit shows "Not evaluated: no source". No separate "entrypoint raised" rule.
- `recording-lost` covers `EGRESS_FAILED`, `EGRESS_ABORTED` and `EGRESS_LIMIT_REACHED`. The console's `alerts-model.ts` matches `EGRESS_FAILED` only today and must widen when this is built.
- Week-2 channels: email and webhook first; Slack, Microsoft Teams, Discord, PagerDuty and Opsgenie follow.
