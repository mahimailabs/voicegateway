# Alerts delivery

## Goal

Send the seven alert rules for real: the worker opens incidents from what each rule matches and delivers every change to email or a webhook, durably, with the behaviour `alerts-behaviour.md` fixed on 2026-10-16. Slack, Microsoft Teams, Discord, PagerDuty and Opsgenie stay listed as coming soon.

## What is built

- **Occurrences.** The worker reads the Issues page's rows (`issue_occurrences`), so an alert and the Issues page never disagree. Issue ids map to rules: `slow` to `slow-answer`, `cut` to `cut-off`, `sip:plain-phone` to `plain-phone` (standing), and `job-failed`, `slow-join`, `recording-lost`, `ingress-error` by name. `alert_occurrences` records each occurrence once per rule, so a redelivered webhook or a re-stored call counts once (check 3). Migration 00023 seeds it from rows already stored, so the first run pages nobody for the past.
- **Incidents.** One firing incident per rule and subject (a unique partial index). The subject is the agent for call and job rules (`call_summaries.agent_key`, else the job's agent name; "Unknown agent" when neither is reported), and the trunk, egress or ingress otherwise. An incident resolved less than 10 minutes ago is reopened instead of replaced, with no second firing message.
- **Recovery.** Call rules resolve after 5 or more calls in the last 30 minutes with none matching; fewer hold the incident with "fewer than 5 calls in the last 30 minutes". `job-failed` resolves on a later successful job, `slow-join` after 3 joins within the threshold, `plain-phone` when no trunk is plain any more, `ingress-error` on a later non-error ingress state. `recording-lost` is resolved by a person (Member or Admin) only. A source silent for 30 minutes marks the incident `silent_since` and never resolves it (check 7).
- **Reminders.** At most one every 4 hours while new occurrences arrive, carrying the count since the last message; a high rule also reminds when the count doubles.
- **Delivery.** Each change is one `alert_deliveries` row per channel, claimed with a lease (`FOR UPDATE SKIP LOCKED`) so a crashed worker's sends are retried. Retries wait 30 s, then double to a 30 minute cap, for 24 hours, honouring `Retry-After`. 2xx is delivered; 408, 429 and 5xx retry (check 5); any other answer, redirects included, fails at once and marks the channel broken (check 6), as does an address the worker's guard refuses (on Cloud, any private address). A delivery that finally fails adds "Alert not delivered" to the Issues page. The provider's message is kept to 200 printable characters with the URL, its path parts, query values and the secret removed.
- **Webhooks** are signed with the Standard Webhooks library (`webhook-id`, `webhook-timestamp`, `webhook-signature`); `webhook-id` and `Idempotency-Key` are `incident/change-N/channel`, the same on every retry. The URL and the `whsec_` secret are sealed in Postgres under `VOICEGATEWAY_SECRET_KEY`; the API answers only the host, and the secret once, when it is made. Sends dial through the worker's address guard and never follow redirects.
- **Email** goes through the existing SMTP mailer (`VOICEGATEWAY_SMTP_URL`), one message to 1 to 20 recipients. Without SMTP the email channel cannot be set up.
- **Off rules** still open incidents and send nothing; the firing message goes out if the rule later sends while the incident is still firing.
- **Retention.** Incidents and their deliveries 30 days; the occurrence marks 90 days, as long as raw call data.
- **API.** `getAlertSetup` (rules, channels with their latest delivery, `emailAvailable`), `updateAlertRule` (Member), `updateAlertChannel`, `deleteAlertChannel`, `testAlertChannel` (Admin), `listAlertIncidents`, `resolveAlertIncident` (Member, `recording-lost` only, else 409 `incident_not_resolvable`).
- **Console.** Rule switches and channel picks save on the server; rule cards show firing incidents, held and silent reasons, and "Not delivered to X" when a channel is failing. History lists incidents with every delivery. Settings, Integrations sets the webhook and email channels, shows the secret once, sends a test and shows the last delivery.

## Acceptance checks

The checks are `alerts-behaviour.md` 3 to 7, run against Postgres 16 through testcontainers (`internal/alert/alert_test.go`).

3. The same webhook stored twice gives one occurrence and one firing message: `TestRedeliveredWebhookFiresOnce`.
4. 10 slow calls in an hour on one agent give one incident and one firing message, then reminders on the schedule: `TestSlowCallsGroupIntoOneIncident`.
5. A channel answering 500 three times then 200 gets one delivered message with 3 failed attempts, the same `webhook-id` and a valid signature on each: `TestRetriesUntilDelivered`; the schedule itself: `TestRetrySchedule`.
6. A channel answering 400 gets no retry, is marked broken and adds an Issues row without the secret: `TestRefusalBreaksChannel`; the console names it on the rule (`dashboard/tests/alerts.test.tsx`).
7. A silent source keeps its incident firing, marked silent: `TestSilentSourceStaysFiring`.

Also: `TestCleanCallsResolveAndFlapReopens`, `TestDisabledRuleSendsNothing`, `TestPlainTrunkCountsOncePerActivation`, `TestAttemptRecordedAfterTickEnds`, `TestAlertSetupKeepsChannelSecrets` and `TestAlertReads` (contract-validated).

8. End to end on the Mac LiveKit slot: a real slow call on a LiveKit Cloud project reaches a webhook receiver once, signed, and resolves after clean calls. Not run yet.
