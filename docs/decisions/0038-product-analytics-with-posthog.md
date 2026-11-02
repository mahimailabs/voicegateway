# 0038: Product analytics with PostHog, off unless an operator turns it on

- Status: accepted
- Date: 2026-10-31

## Context

Cloud needs to know how people move through it: which pages they use, and how far each new workspace gets through setup (project created, LiveKit connected, agent connected, first call). On 2026-10-31 Mahimai connected a PostHog organisation for this. Sentry is being added at the same time for errors, so this decision covers usage only.

VoiceGateway is self-hosted first, and the console shows what callers and agents said. Whatever we add must send nothing from a self-hosted install that did not ask for it, and never call content or personal data from any install.

## Options

1. **PostHog through its official SDKs (posthog-js in the console, posthog-go on the server).** The account exists; one tool gives page views, funnels, retention and, later, feature flags. Hosted in the US or EU, or self-hosted by an operator who wants that.
2. **Plausible or Umami.** Page views only; no per-user funnels or retention, which is the question we have.
3. **Our own events in ClickHouse.** Already in the stack, but every chart, funnel and retention query would be ours to build and keep.
4. **Google Analytics 4.** Ad-oriented, with consent requirements, and a poor fit for a product people sign in to.

## Decision

Option 1, with these rules:

- **Off by default.** `VOICEGATEWAY_POSTHOG_KEY` (a project key, `phc_...`, which is public by design) turns it on; `VOICEGATEWAY_POSTHOG_HOST` names the host and defaults to `https://us.i.posthog.com`. A personal key (`phx_...`) is refused at start, since it would reach the browser. The compose file leaves both empty.
- **One read.** `GET /auth/client-config` tells the console, before sign-in, what it may know of the install: `{"posthog": null | {"key", "host"}}`. Sentry's browser settings join the same answer.
- **Lazy.** posthog-js loads in its own chunk, only when the key is set, and never in an embedded widget or on the mock API. Analytics failing never stops the console.
- **What is sent.** Page views by path, page leaves, and a named list of events (`sign_in_started`, `signed_in`, `invitation_accepted`, `project_created`, `setup_step_reached`, `setup_deferred`), each with only the properties its type allows. Users are identified by their VoiceGateway user id, which `GET /auth/session` now returns; anonymous visitors get no person profile.
- **What is never sent.** Emails, names, call content, transcripts, URL queries and fragments (stripped in `before_send`, since a calls filter can hold what a caller said), clicks and their text (autocapture is off), screen recordings, heatmaps, exceptions (Sentry's), and flag or survey requests. No script is loaded from PostHog's CDN.
- **Server events.** The Cloud control plane records the paid sign-up steps it alone sees (checkout completed, workspace provisioned, subscription cancelled) with posthog-go, keyed by organisation id, with no email or Stripe ids.

## Consequences

- A self-hosted operator can point the console at their own PostHog project and see their team's usage; nothing goes to us unless they do.
- IP addresses still reach PostHog with each request, as with any web request. The Cloud project should turn on PostHog's "Discard client IP data" setting, after which PostHog records no location.
- Feature flags are off (`advanced_disable_flags`). Turning them on later is one config change and a new decision on what flags may target.
- Ad blockers drop some events. A reverse proxy on the console's own domain (`VOICEGATEWAY_POSTHOG_HOST=https://<console>/ingest`) fixes that when it matters.
- New console events must be added to the `AnalyticsEvent` type in `dashboard/src/lib/analytics.ts`, which keeps the list above the whole truth.
