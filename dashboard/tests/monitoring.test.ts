import { afterEach, expect, test, vi } from "vitest"
import type { Event } from "@sentry/react"
import { fetchClientConfig } from "../src/lib/client-config.ts"
import { scrubBreadcrumb, scrubEvent, scrubSpan, scrubUrl } from "../src/lib/monitoring.ts"

// Error reports leave the install, so nothing in them may name a person or carry a credential.
afterEach(() => vi.unstubAllGlobals())

test.each([
  ["an embed token in the query", "https://vg.example/org/o/project/p/embed/calls?token=embed-secret", "https://vg.example/org/o/project/p/embed/calls"],
  ["an invitation in the fragment", "https://vg.example/invite#invite-secret", "https://vg.example/invite"],
  ["a plain path", "/org/o/project/p/calls", "/org/o/project/p/calls"],
])("the address loses %s", (_name, raw, want) => {
  expect(scrubUrl(raw)).toBe(want)
})

test("an event keeps its path and loses the user, the request's data and the breadcrumbs' tokens", () => {
  const event: Event = {
    user: { email: "ada@example.com", ip_address: "203.0.113.9" },
    request: { url: "https://vg.example/calls?token=t1", query_string: "token=t1", cookies: { session: "s1" }, headers: { Authorization: "Bearer k1" }, data: "transcript" },
    breadcrumbs: [{ category: "fetch", data: { url: "/api/v1/calls?token=t2", status_code: 200 } }, { category: "navigation", data: { from: "/invite#t3", to: "/" } }],
  }
  const raw = JSON.stringify(scrubEvent(event))
  for (const secret of ["ada@", "203.0.113.9", "t1", "s1", "k1", "transcript", "t2", "t3"]) expect(raw).not.toContain(secret)
  expect(event.request?.url).toBe("https://vg.example/calls")
  expect(event.breadcrumbs?.[0].data?.status_code).toBe(200)
})

test("a span keeps its path and loses the query string and fragment", () => {
  const span = scrubSpan({
    trace_id: "t",
    span_id: "s",
    name: "GET /api/v1/calls?token=t1",
    start_timestamp: 0,
    status: "ok",
    is_segment: true,
    attributes: { "url.full": "https://vg.example/invite#t2", "http.url": { value: "/embed?token=t3", type: "string" }, "url.query": "token=t4", "http.fragment": "t5", "sentry.op": "pageload" },
  })
  const raw = JSON.stringify(span)
  for (const secret of ["t1", "t2", "t3", "t4", "t5"]) expect(raw).not.toContain(secret)
  expect(span.name).toBe("GET /api/v1/calls")
  expect(span.attributes["url.full"]).toBe("https://vg.example/invite")
  expect(span.attributes["sentry.op"]).toBe("pageload")
})

test("a breadcrumb without data is unchanged", () => {
  const crumb = { category: "ui.click", message: "button" }
  expect(scrubBreadcrumb(crumb)).toBe(crumb)
})

test.each([
  ["no client config route", async () => new Response("Not Found", { status: 404 })],
  ["the dev server's page", async () => new Response("<!doctype html>", { status: 200 })],
  ["no DSN", async () => Response.json({ sentry: { dsn: "" } })],
  ["Sentry off", async () => Response.json({ sentry: null })],
  ["no network", async () => Promise.reject(new TypeError("Failed to fetch"))],
])("%s leaves reporting off", async (_name, answer) => {
  vi.stubGlobal("fetch", answer)
  expect(await fetchClientConfig()).toEqual({ sentry: null, posthog: null })
})

test("a console DSN turns reporting on", async () => {
  vi.stubGlobal("fetch", async () => Response.json({ sentry: { dsn: "https://k@o1.ingest.us.sentry.io/2", environment: "production", tracesSampleRate: 0.1 } }))
  expect(await fetchClientConfig()).toEqual({ sentry: { dsn: "https://k@o1.ingest.us.sentry.io/2", environment: "production", tracesSampleRate: 0.1 }, posthog: null })
})
