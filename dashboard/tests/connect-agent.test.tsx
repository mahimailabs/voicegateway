// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from "vitest"
import type { CallPage, ConnectionSource, ProjectConnection } from "../src/lib/api-types"
import { handlers } from "../src/mocks/handlers.ts"
import { totals } from "../src/mocks/calls-query.ts"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"
import { ConnectAgentPanel } from "../src/features/connection/connect-agent.tsx"
import { RECENT_MS, anyError, fixText, keepPolling, sourceState } from "../src/features/connection/connection-model.ts"
import { CONNECTION_POLL_MS } from "../src/lib/api.ts"
import { REQUEST_CAPACITY } from "../src/lib/support.ts"

// Acceptance check 8 of first-call setup: each step state of the "Connect your agent" panel.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})
// The globe draws with WebGL, which jsdom has not got.
vi.mock("../src/components/network-globe.tsx", () => ({ NetworkGlobe: () => null }))
const { OverviewPage } = await import("../src/features/overview/overview.tsx")

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  vi.useRealTimers()
})
afterAll(() => server.close())

const project = "*/api/v1/org/:org/project/:project"
const none: ConnectionSource = { lastAcceptedAt: null, lastRejectedAt: null, reason: null, usedKey: null, expectedKey: null }
const urls = { observability: "https://vg.example.com/ingest/default/default-project", webhook: "https://vg.example.com/ingest/default/default-project/livekit/webhook" }
const connection = (over: Partial<ProjectConnection> = {}): ProjectConnection => ({ urls, webhook: none, agent: none, firstCall: null, ...over })

function answer(...bodies: ProjectConnection[]) {
  const seen = { requests: 0 }
  server.use(
    http.get(`${project}/connection`, () => {
      const body = bodies[Math.min(seen.requests, bodies.length - 1)]
      seen.requests++
      return HttpResponse.json(body)
    }),
  )
  return seen
}

function show(page: () => React.ReactNode = () => <ConnectAgentPanel />) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = [
    createRoute({ getParentRoute: () => root, path: "/", component: page }),
    ...["/calls", "/calls/$callId", "/agents", "/issues", "/sfu", "/sip", "/egress", "/ingress", "/project"].map((path) => createRoute({ getParentRoute: () => root, path, component: () => <p>{path}</p> })),
  ]
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: ["/"] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

const step = async (name: string) => screen.findByRole("listitem", { name })
// An instant this many minutes before the test runs; the panel judges states as of its last read.
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()

describe("sourceState", () => {
  const now = Date.parse("2026-10-20T12:00:00Z")
  const at = (ms: number) => new Date(now - ms).toISOString()
  const cases: { name: string; s: Partial<ConnectionSource>; want: string; warning?: boolean }[] = [
    { name: "nothing accepted or refused: waiting", s: {}, want: "waiting" },
    { name: "accepted, never refused: connected", s: { lastAcceptedAt: at(60 * 60_000) }, want: "connected" },
    { name: "accepted after the refusal: connected", s: { lastAcceptedAt: at(60_000), lastRejectedAt: at(120_000), reason: "token_missing" }, want: "connected" },
    { name: "accepted and refused at the same instant: connected", s: { lastAcceptedAt: at(60 * 60_000), lastRejectedAt: at(60 * 60_000), reason: "too_large" }, want: "connected" },
    { name: "refused after a recent accept: connected with a warning", s: { lastAcceptedAt: at(60_000), lastRejectedAt: at(30_000), reason: "key_mismatch" }, want: "connected", warning: true },
    { name: "accept exactly 10 minutes old, then refused: connected with a warning", s: { lastAcceptedAt: at(RECENT_MS), lastRejectedAt: at(1000), reason: "plan_cap" }, want: "connected", warning: true },
    { name: "accept 10 minutes and 1 ms old, then refused: error", s: { lastAcceptedAt: at(RECENT_MS + 1), lastRejectedAt: at(1000), reason: "plan_cap" }, want: "error" },
    { name: "refused, never accepted: error", s: { lastRejectedAt: at(1000), reason: "signature_invalid" }, want: "error" },
  ]
  // After an accept, only a signature-verified refusal can turn a source to Error.
  const unverified = ["token_missing", "key_mismatch", "signature_invalid", "too_large"] as const
  const verifiedReasons = ["grant_missing", "body_changed", "plan_inactive", "plan_cap"] as const
  const old = { lastAcceptedAt: at(7 * 24 * 60 * 60_000), lastRejectedAt: at(1000) }
  for (const reason of unverified) cases.push({ name: `${reason} after an accept a week old: connected with a warning`, s: { ...old, reason }, want: "connected", warning: true })
  cases.push({ name: "a refusal with no reason after an accept a week old: connected with a warning", s: { ...old, reason: null }, want: "connected", warning: true })
  for (const reason of verifiedReasons) {
    cases.push({ name: `${reason} after an accept a week old: error`, s: { ...old, reason }, want: "error" })
    cases.push({ name: `${reason} after an accept within 10 minutes: connected with a warning`, s: { lastAcceptedAt: at(RECENT_MS), lastRejectedAt: at(1000), reason }, want: "connected", warning: true })
  }
  for (const reason of [...unverified, ...verifiedReasons, null]) cases.push({ name: `${reason ?? "no reason"}, never accepted: error`, s: { lastRejectedAt: at(1000), reason }, want: "error" })
  for (const c of cases)
    test(c.name, () => {
      const got = sourceState({ ...none, ...c.s }, now)
      expect(got.state).toBe(c.want)
      if (got.state === "connected") expect(got.refused !== null).toBe(c.warning === true)
      if (got.state === "connected" && got.refused) expect(got.refused).toEqual({ at: c.s.lastRejectedAt, reason: c.s.reason })
    })
})

describe("keepPolling", () => {
  const now = Date.parse("2026-10-20T12:00:00Z")
  const first = { id: "8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b", startedAt: null }
  const refused: ConnectionSource = { ...none, lastRejectedAt: "2026-10-20T11:59:00Z", reason: "plan_cap" }
  const accepted: ConnectionSource = { ...none, lastAcceptedAt: "2026-10-20T11:59:00Z" }
  const cases: { name: string; c: ProjectConnection | undefined; want: boolean }[] = [
    { name: "nothing read yet", c: undefined, want: true },
    { name: "waiting, no call", c: connection(), want: true },
    { name: "connected, no call", c: connection({ webhook: accepted, agent: accepted }), want: true },
    { name: "first call, every source connected", c: connection({ webhook: accepted, agent: accepted, firstCall: first }), want: false },
    { name: "first call, a source waiting", c: connection({ webhook: accepted, firstCall: first }), want: false },
    { name: "first call, a source in error", c: connection({ webhook: accepted, agent: refused, firstCall: first }), want: true },
    {
      name: "first call, a refusal after a recent accept",
      c: connection({ webhook: accepted, agent: { ...accepted, lastRejectedAt: "2026-10-20T11:59:30Z", reason: "token_missing" }, firstCall: first }),
      want: true,
    },
    {
      name: "first call, an unverified refusal after an accept a day old",
      c: connection({ webhook: accepted, agent: { ...none, lastAcceptedAt: "2026-10-19T12:00:00Z", lastRejectedAt: "2026-10-20T11:59:30Z", reason: "signature_invalid" }, firstCall: first }),
      want: true,
    },
  ]
  for (const c of cases) test(c.name, () => expect(keepPolling(c.c, now)).toBe(c.want))
  test("anyError sees an error on either source", () => {
    expect(anyError(connection({ webhook: refused }), now)).toBe(true)
    expect(anyError(connection({ agent: refused }), now)).toBe(true)
    expect(anyError(connection({ webhook: accepted, agent: accepted }), now)).toBe(false)
    expect(anyError(connection({ agent: { ...none, lastAcceptedAt: "2026-10-19T12:00:00Z", lastRejectedAt: "2026-10-20T11:59:30Z", reason: "token_missing" } }), now)).toBe(false)
    expect(anyError(connection({ agent: { ...none, lastAcceptedAt: "2026-10-19T12:00:00Z", lastRejectedAt: "2026-10-20T11:59:30Z", reason: "plan_inactive" } }), now)).toBe(true)
  })
})

describe("step state", () => {
  test("waiting: nothing accepted or refused yet, and no call stored", async () => {
    answer(connection())
    show()
    for (const name of ["Webhook", "Agent upload", "First call stored"]) expect(within(await step(name)).getByText("Waiting")).toBeTruthy()
    expect(within(await step("First call stored")).getByText("No call stored yet. Make a call to your agent.")).toBeTruthy()
  })

  test("connected: the last accepted request is newer than the last refused one", async () => {
    answer(connection({ webhook: { ...none, lastAcceptedAt: ago(1), lastRejectedAt: ago(2), reason: "token_missing" }, agent: { ...none, lastAcceptedAt: ago(60) } }))
    show()
    expect(within(await step("Webhook")).getByText("Connected")).toBeTruthy()
    expect(within(await step("Agent upload")).getByText("Connected")).toBeTruthy()
    expect(within(await step("Webhook")).queryByText(/no LiveKit token/)).toBeNull()
  })

  test("connected with a warning: refused after an accept within the last 10 minutes", async () => {
    answer(connection({ agent: { ...none, lastAcceptedAt: ago(5), lastRejectedAt: ago(1), reason: "token_missing" } }))
    show()
    const agent = await step("Agent upload")
    expect(within(agent).getByText("Connected")).toBeTruthy()
    expect(within(agent).queryByText("Error")).toBeNull()
    const warning = within(agent).getByRole("status")
    expect(warning.textContent).toMatch(/Last request refused: The upload carried no LiveKit token/)
    expect(warning.textContent).toMatch(/LIVEKIT_API_KEY and LIVEKIT_API_SECRET/)
    expect(warning.textContent).toMatch(/Refused /)
  })

  test("error: a verified refusal after an accept older than 10 minutes, or any refusal with no accept", async () => {
    answer(connection({ webhook: { ...none, lastAcceptedAt: ago(30), lastRejectedAt: ago(1), reason: "body_changed" }, agent: { ...none, lastRejectedAt: ago(1), reason: "grant_missing" } }))
    show()
    const webhook = await step("Webhook")
    expect(within(webhook).getByText("Error")).toBeTruthy()
    expect(within(webhook).getByText(/rewrote the body\. Send it unmodified\./)).toBeTruthy()
    const agent = await step("Agent upload")
    expect(within(agent).getByText("Error")).toBeTruthy()
    expect(within(agent).getByText(/Python livekit-agents 1\.8\.6 or later, or Node @livekit\/agents 1\.8\.0 or later/)).toBeTruthy()
  })

  test("an unverified refusal after an old accept stays a warning", async () => {
    answer(connection({ webhook: { ...none, lastAcceptedAt: ago(24 * 60), lastRejectedAt: ago(1), reason: "token_missing" } }))
    show()
    const webhook = await step("Webhook")
    expect(within(webhook).getByText("Connected")).toBeTruthy()
    expect(within(webhook).queryByText("Error")).toBeNull()
    expect(within(webhook).getByRole("status").textContent).toMatch(/Add the webhook URL in LiveKit/)
  })

  test("key_mismatch names the key used and the key expected, as code", async () => {
    answer(connection({ agent: { ...none, lastRejectedAt: ago(1), reason: "key_mismatch", usedKey: "APIother123", expectedKey: "APIthis456" } }))
    show()
    const agent = await step("Agent upload")
    expect(within(agent).getByText("Error")).toBeTruthy()
    expect(within(agent).getByText(/^Used key/).textContent).toBe("Used key APIother123, this project expects APIthis456.")
    expect(within(agent).getByText("APIother123").tagName).toBe("CODE")
    expect(within(agent).getByText("APIthis456").tagName).toBe("CODE")
  })

  test("key_mismatch with an unreadable key says so", async () => {
    answer(connection({ agent: { ...none, lastRejectedAt: ago(1), reason: "key_mismatch", usedKey: null, expectedKey: "APIthis456" } }))
    show()
    const agent = await step("Agent upload")
    expect(within(agent).getByText(/an unreadable key/).textContent).toBe("The token named an unreadable key, this project expects APIthis456.")
  })

  test("plan_cap offers the usage allowance's capacity link", async () => {
    answer(connection({ webhook: { ...none, lastRejectedAt: ago(1), reason: "plan_cap" } }))
    show()
    const webhook = await step("Webhook")
    expect(within(webhook).getByText("Telemetry ingestion paused")).toBeTruthy()
    expect(within(webhook).getByRole("link", { name: REQUEST_CAPACITY.label }).getAttribute("href")).toBe(REQUEST_CAPACITY.href)
  })

  test("first call stored links to that call", async () => {
    answer(connection({ firstCall: { id: "8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b", startedAt: "2026-10-20T10:02:00Z" } }))
    show()
    const first = await step("First call stored")
    expect(within(first).getByText("Connected")).toBeTruthy()
    expect(within(first).getByRole("link", { name: "Open the first call" }).getAttribute("href")).toBe("/calls/8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b")
  })
})

test("each URL has a copy button", async () => {
  answer(connection())
  show()
  expect(await screen.findByRole("button", { name: "Copy agent upload url" })).toBeTruthy()
  expect(screen.getByRole("button", { name: "Copy webhook url" })).toBeTruthy()
  expect(screen.getByLabelText("Agent upload URL").textContent).toBe(urls.observability)
  expect(screen.getByLabelText("Webhook URL").textContent).toBe(urls.webhook)
  expect(screen.getByText("Set LIVEKIT_OBSERVABILITY_URL in your agent to this value.")).toBeTruthy()
  expect(screen.queryByText(/VOICEGATEWAY_PUBLIC_URL/)).toBeNull()
})

test("null URLs say once how to set VOICEGATEWAY_PUBLIC_URL, and offer nothing to copy", async () => {
  answer(connection({ urls: { observability: null, webhook: null } }))
  show()
  await waitFor(() => expect(screen.getAllByText(/set VOICEGATEWAY_PUBLIC_URL in deploy\/\.env/)).toHaveLength(1))
  expect(screen.getByText(/then run make up\./)).toBeTruthy()
  expect(screen.queryByText(/restart web/)).toBeNull()
  expect(screen.queryByRole("button", { name: /^Copy/ })).toBeNull()
})

test("every reason has its own fix, and the source-specific ones differ by source", () => {
  const reasons = ["token_missing", "key_mismatch", "signature_invalid", "grant_missing", "body_changed", "plan_inactive", "plan_cap", "too_large"] as const
  for (const source of ["agent", "webhook"] as const) {
    const texts = reasons.map((reason) => fixText(source, { ...none, reason }).fix)
    expect(new Set(texts).size).toBe(reasons.length)
    for (const reason of reasons) {
      const { title, fix } = fixText(source, { ...none, reason })
      expect(`${title} ${fix}`).not.toMatch(/\u2014|billing|add minutes/i)
      expect(fix).not.toBe(title)
    }
  }
  for (const reason of ["token_missing", "signature_invalid", "too_large"] as const) expect(fixText("agent", { ...none, reason }).fix).not.toBe(fixText("webhook", { ...none, reason }).fix)
  expect(fixText("agent", { ...none, reason: "token_missing" }).fix).toMatch(/LIVEKIT_API_KEY and LIVEKIT_API_SECRET/)
  expect(fixText("webhook", { ...none, reason: "token_missing" }).fix).toMatch(/Add the webhook URL in LiveKit/)
  expect(fixText("agent", { ...none, reason: "signature_invalid" }).fix).toMatch(/not a LiveKit token\. Set LIVEKIT_API_SECRET in the agent/)
  expect(fixText("webhook", { ...none, reason: "signature_invalid" }).fix).toMatch(/must match the LiveKit project that signs the webhook/)
  expect(fixText("webhook", { ...none, reason: "too_large" }).fix).toMatch(/proxy/)
  expect(fixText("agent", { ...none, reason: "too_large" }).fix).toMatch(/Open an issue/)
  expect(fixText("agent", { ...none, reason: "plan_cap" }).capacity).toBe(true)
  expect(fixText("agent", { ...none, reason: "plan_inactive" }).capacity).toBe(true)
})

describe("polling", () => {
  const first = { id: "8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b", startedAt: null }

  test("every 5 s until the first call is stored, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const seen = answer(connection(), connection({ firstCall: first }))
    show()
    expect(within(await step("First call stored")).getByText("Waiting")).toBeTruthy()
    expect(seen.requests).toBe(1)
    await act(async () => vi.advanceTimersByTimeAsync(CONNECTION_POLL_MS))
    await waitFor(() => expect(within(screen.getByRole("listitem", { name: "First call stored" })).getByText("Connected")).toBeTruthy())
    expect(seen.requests).toBe(2)
    await act(async () => vi.advanceTimersByTimeAsync(CONNECTION_POLL_MS * 4))
    expect(seen.requests).toBe(2)
  })

  test("keeps going after the first call while a source is in error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const seen = answer(connection({ firstCall: first, agent: { ...none, lastRejectedAt: ago(1), reason: "plan_cap" } }))
    show()
    expect(within(await step("Agent upload")).getByText("Error")).toBeTruthy()
    expect(seen.requests).toBe(1)
    await act(async () => vi.advanceTimersByTimeAsync(CONNECTION_POLL_MS * 3))
    await waitFor(() => expect(seen.requests).toBe(4))
  })
})

describe("on Overview", () => {
  const empty: CallPage = { items: [], page: { nextCursor: null, snapshot: "s" }, totals: totals([], 1500) }
  const headings = () => screen.getAllByRole("heading").map((h) => `${h.tagName} ${h.textContent}`)

  test("a project with no calls shows the panel under the page heading", async () => {
    answer(connection())
    server.use(http.get(`${project}/calls`, () => HttpResponse.json(empty)))
    show(OverviewPage)
    expect(await screen.findByRole("heading", { name: "Connect your agent" })).toBeTruthy()
    const h1 = await screen.findByRole("heading", { level: 1, name: "Overview" })
    const h2 = screen.getByRole("heading", { name: "Connect your agent" })
    expect(h1.compareDocumentPosition(h2) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(headings().indexOf("H1 Overview")).toBe(0)
  })

  test("a project with calls and no source in error does not show it", async () => {
    answer(connection())
    show(OverviewPage)
    expect(await screen.findByText("Overview")).toBeTruthy()
    expect(screen.queryByRole("heading", { name: "Connect your agent" })).toBeNull()
  })

  test("a project with calls shows it while a source is in error", async () => {
    answer(connection({ firstCall: { id: "8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b", startedAt: null }, agent: { ...none, lastRejectedAt: ago(1), reason: "key_mismatch", usedKey: "APIold", expectedKey: "APInew" } }))
    show(OverviewPage)
    expect(await screen.findByRole("heading", { name: "Connect your agent" })).toBeTruthy()
    expect(within(await step("Agent upload")).getByText("Error")).toBeTruthy()
  })
})
