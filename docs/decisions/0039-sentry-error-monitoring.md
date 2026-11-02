# 0039: VoiceGateway reports its own errors to Sentry, off unless a DSN is set

- Status: accepted
- Date: 2026-10-31

## Context

Cloud runs one shared instance (decision 0028), and when web, the worker or the console fails, we learn of it from a customer or from the host's logs. Mahimai asked to wire Sentry in on 2026-10-31; the Sentry organisation `voicegateway` is on the Business plan. The same image serves self-hosted installs, which must not send anything to us or to anyone they did not choose.

## Options

1. **Sentry SDKs, configured at runtime.** `getsentry/sentry-go` for web and worker, `@sentry/react` for the console, each off until its DSN is set. The console is one build for every install, so web hands the browser its DSN at runtime (`GET /auth/client-config`).
2. **OpenTelemetry to Sentry's OTLP endpoint.** Vendor-neutral, but errors as span events lose Sentry's issue grouping, stack traces and release tracking, and the console would still need the browser SDK.
3. **DSN built into the console with `VITE_SENTRY_DSN`.** Every self-hosted install would then report to our project. Refused.
4. **Logs only (Sentry Logs through `sentry-go/slog`).** As of sentry-go 0.50 that handler sends log entries, not issues: no alerting on new errors.

## Decision

Option 1.

- **Go:** sentry-go v0.50.0. Error-level slog records become Sentry events through `samber/slog-sentry` v2.11.0 (the handler sentry-go's own integration was derived from, which still creates events); every role's terminal error and a panic in its main goroutine are reported too. `sentryhttp` wraps web's handler for request timings and panics. `/healthz` is never timed.
- **Console:** `@sentry/react` 11.8.0, imported only when a DSN is set, so a self-hosted console never loads it. Page timings are named by route through the TanStack Router integration. No Session Replay.
- **What leaves the install:** `DataCollection` in both SDKs turns off cookies, query strings, bodies and user details, and keeps only the Content-Type, User-Agent and Accept header values. The console also drops the query string and fragment of every URL (embed tokens ride in `?token=`, invitations in `#`) and sends no console breadcrumbs. No code path puts a transcript, audio or a LiveKit secret into a report, but error messages and log attributes are sent as written, so code must not put those values into an error or an Error-level log. Reports also carry header names; only those three keep their values, the rest read `[Filtered]`.
- **Settings:** `VOICEGATEWAY_SENTRY_DSN`, `VOICEGATEWAY_SENTRY_CONSOLE_DSN`, `VOICEGATEWAY_SENTRY_ENVIRONMENT` (default `production`), `VOICEGATEWAY_SENTRY_TRACES_SAMPLE_RATE` (default 0.1). Two DSNs, because Sentry keeps a Go and a browser platform per project.

## Consequences

- Cloud sets the DSNs in its host's environment; they are never committed. Sentry projects: `app-api` (Go) and `app-web` (React).
- Transaction names for routes under `/api/v1/` read `/api/v1/`: `sentryhttp` names them from the outer ServeMux pattern, not chi's route. Finer names need a span per chi route, later.
- Errors logged below error level are not reported; a log level change is a reporting change.
- Source maps are not uploaded yet, so console stack traces show minified names until a release step uploads them.
