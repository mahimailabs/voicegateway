# Alert channels

## Goal

Deliver every alert change (firing, reminder, resolved, test) to Slack, Microsoft Teams, Discord, PagerDuty and Opsgenie, next to email and the signed webhook, with the delivery, retry and redaction rules of `alerts-delivery.md` unchanged. Roadmap #320, lane 1 of the V1 finish plan.

## Channels

| Kind | An Admin enters | Sent as | Library |
| --- | --- | --- | --- |
| `slack` | The incoming webhook URL, `https://hooks.slack.com/services/...` (or `hooks.slack-gov.com`) | Block Kit: a header, the facts as plain-text fields, buttons to the call and the alert history; the fallback `text` escapes `<`, `>` and `&` | `slack-go/slack` types |
| `teams` | A Teams Workflows webhook URL on `*.logic.azure.com` or `*.api.powerplatform.com` (Office 365 connectors are retired) | An Adaptive Card: title, latest occurrence, a fact set, Open URL actions | `atc0005/go-teams-notify/v2/adaptivecard` |
| `discord` | The channel webhook URL, `https://discord.com/api/webhooks/<id>/<token>` (also `discordapp.com`, `ptb.`, `canary.`) | One embed with the facts as fields; `allowed_mentions.parse` is empty, so nothing pings anyone | `bwmarrin/discordgo` types |
| `pagerduty` | An Events API v2 integration (routing) key, 32 characters, and a region, `us` or `eu` | Firing and reminders `trigger` with dedup key `voicegateway-<incident>`, so PagerDuty keeps one incident; resolved `resolve`s it; a test is a change event, which pages nobody | `PagerDuty/go-pagerduty` client |
| `opsgenie` | An API integration key (a UUID) and a region, `us` or `eu` | Firing and reminders create the alert under alias `voicegateway-<incident>`, which Opsgenie de-duplicates; resolved closes it by alias; a test creates a P5 alert | `opsgenie/opsgenie-go-sdk-v2` |

Severity maps to PagerDuty `critical` (high) or `warning` (medium), and to Opsgenie `P2` or `P3`.

The URL or key is sealed in `alert_channels.config_sealed` under `VOICEGATEWAY_SECRET_KEY`, like a webhook's, and never returned; the console shows the host each channel posts to. Slack, Teams and Discord URLs must be https on the provider's host (a pasted URL from the wrong provider is refused before anything is sent). Every send dials through the worker's address guard.

Opsgenie is end of life at Atlassian (support ends April 2027), so it ships as create and close only.

## Delivery

Chat channels are POSTed through the same code as the webhook: 2xx is delivered; 408, 429 and 5xx retry on the 30 s to 30 min schedule for 24 hours, honouring `Retry-After`; any other answer is final and marks the channel broken. PagerDuty and Opsgenie go through their SDKs, with the same classification of the HTTP status they report; neither SDK retries on its own. A provider's message is kept to 200 printable characters with the URL's parts and the key removed.

## Follow-ups from #454

- **Recipients are for Admins.** `getAlertSetup` answers `recipients` only to an Admin; a Member, Viewer, API key or embed sees the count in `target`.
- **https only on Cloud.** With `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true` web refuses a plain http webhook URL, and the worker refuses one saved before (`address_not_allowed`).
- **Removing a channel cancels its queue.** `deleteAlertChannel` marks its pending and retrying deliveries `cancelled` (status `channel_removed`) in the same transaction. A delivery being sent at that moment stays cancelled whatever the channel answers, and nothing is added to the Issues page or marks a channel broken. Migration 00024 adds the kinds and the `cancelled` state.

## Acceptance checks

1. Each kind saves only its own URL or key shape and answers only the host: `TestAlertChannelKinds`.
2. A Viewer reads no email recipients; Cloud refuses a plain http webhook: `TestAlertChannelKinds`, `TestHTTPSOnlyRefusesPlainHTTP`.
3. Slack, Discord and Teams messages carry call text as plain text, never markup or mentions: `TestChatMessagesCarryNoMarkup`.
4. A Slack channel receives a firing incident end to end through Postgres: `TestSlackChannelDelivers`.
5. PagerDuty triggers and resolves under one dedup key, tests with a change event, and treats 400 as final and 429 as retry, without the key in the message: `TestPagerDutyEvents`.
6. Opsgenie creates under the incident alias, closes by alias, tests at P5, and treats 422 as final: `TestOpsgenieAlerts`.
7. A removed channel's queued and in-flight deliveries end cancelled, with no Issues row: `TestRemovedChannelCancelsQueuedDeliveries`.
8. The console saves a Slack URL and a PagerDuty key with its region, and shows neither again: `dashboard/tests/alerts.test.tsx`.
9. Real workspaces: one test alert to each of a Slack, Teams and Discord channel, a PagerDuty service and an Opsgenie team. Not run yet; needs the accounts.
