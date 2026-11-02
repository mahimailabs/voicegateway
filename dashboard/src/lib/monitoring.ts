import type { Breadcrumb, BrowserOptions, Event } from "@sentry/react"
import type { AnyRouter } from "@tanstack/react-router"

// Error and performance reporting to Sentry (decision 0039). Off unless web's client config names a
// console DSN, as on every self-hosted install; then the SDK loads as its own chunk. No session
// replay and no console breadcrumbs: the console shows transcripts, and neither may leave the page.

export type SentryClientConfig = { dsn: string; environment: string; tracesSampleRate: number }

/** The address without its query string and fragment: an embed token rides in ?token=, an invitation in #. */
export function scrubUrl(raw: string): string {
  const cut = raw.search(/[?#]/)
  return cut === -1 ? raw : raw.slice(0, cut)
}

/** Drops what an event could carry of a person or a credential before it is sent. */
export function scrubEvent<E extends Event>(event: E): E {
  delete event.user
  if (event.request) {
    if (event.request.url) event.request.url = scrubUrl(event.request.url)
    delete event.request.query_string
    delete event.request.cookies
    delete event.request.headers
    delete event.request.data
  }
  event.breadcrumbs = event.breadcrumbs?.map(scrubBreadcrumb)
  return event
}

export function scrubBreadcrumb(crumb: Breadcrumb): Breadcrumb {
  const data = crumb.data
  if (!data) return crumb
  const next = { ...data }
  for (const key of ["url", "from", "to"]) if (typeof next[key] === "string") next[key] = scrubUrl(next[key])
  return { ...crumb, data: next }
}

type StreamedSpan = Parameters<NonNullable<BrowserOptions["beforeSendSpan"]>>[0]

const URL_PARTS = new Set(["url.query", "url.fragment", "http.query", "http.fragment"])

/** Cuts the query string and fragment out of a span's name and of every url.* and http.* attribute. */
export function scrubSpan(span: StreamedSpan): StreamedSpan {
  const attributes: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(span.attributes)) {
    if (URL_PARTS.has(key)) continue
    if (key === "url" || key.startsWith("url.") || key.startsWith("http.")) attributes[key] = scrubValue(value)
    else attributes[key] = value
  }
  return { ...span, name: scrubUrl(span.name), attributes: attributes as StreamedSpan["attributes"] }
}

function scrubValue(value: unknown): unknown {
  if (typeof value === "string") return scrubUrl(value)
  if (value && typeof value === "object" && "value" in value && typeof value.value === "string") return { ...value, value: scrubUrl(value.value) }
  return value
}

let sentry: Promise<typeof import("@sentry/react") | null> = Promise.resolve(null)

/** Starts Sentry when config has a DSN. Never throws: a failed start only leaves reporting off. */
export function startMonitoring(config: SentryClientConfig | null | undefined): Promise<unknown> {
  if (!config?.dsn) return sentry
  sentry = import("@sentry/react").then(
    (Sentry) => {
      Sentry.init({
        dsn: config.dsn,
        environment: config.environment,
        tracesSampleRate: config.tracesSampleRate,
        // Nothing of a person or a request beyond its path: no user, cookies, headers, bodies or query.
        dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false },
        // Traces never propagate to other origins.
        tracePropagationTargets: [/^\//],
        integrations: (defaults) => defaults.filter((i) => i.name !== "Console"),
        beforeSend: scrubEvent,
        // Spans stream on their own in this SDK; beforeSendTransaction only sees the static lifecycle.
        beforeSendSpan: scrubSpan,
        beforeSendTransaction: scrubEvent,
        beforeBreadcrumb: scrubBreadcrumb,
      })
      return Sentry
    },
    (e: unknown) => {
      console.error("Error reporting did not start.", e)
      return null
    },
  )
  return sentry
}

/** Names page-load and navigation timings by route, once the router exists. */
export async function traceRouter(router: AnyRouter): Promise<void> {
  const Sentry = await sentry
  Sentry?.getClient()?.addIntegration(Sentry.tanstackRouterBrowserTracingIntegration(router))
}

/**
 * React's root error callbacks: an error a route's error boundary catches never reaches the
 * window's handlers, so it is reported here. Each still logs, as React does without them.
 */
export const rootErrorHandlers = {
  onCaughtError: (error: unknown, info: { componentStack?: string }) => report(error, info),
  onUncaughtError: (error: unknown, info: { componentStack?: string }) => report(error, info),
}

function report(error: unknown, info: { componentStack?: string }) {
  console.error(error)
  void sentry.then((Sentry) => Sentry?.captureException(error, { contexts: { react: { componentStack: info.componentStack ?? "" } } }))
}
