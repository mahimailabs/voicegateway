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
import { fixText, sourceState } from "../src/features/connection/connection-model.ts"
import { CONNECTION_POLL_MS } from "../src/lib/api.ts"

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

describe("step state", () => {
  test("waiting: nothing accepted or refused yet, and no call stored", async () => {
    answer(connection())
    show()
    for (const name of ["Webhook", "Agent upload", "First call stored"]) expect(within(await step(name)).getByText("Waiting")).toBeTruthy()
    expect(within(await step("First call stored")).getByText("No call stored yet. Make a call to your agent.")).toBeTruthy()
  })

  test("connected: the last accepted request is newer than the last refused one", async () => {
    answer(connection({ webhook: { ...none, lastAcceptedAt: "2026-10-20T10:01:00Z", lastRejectedAt: "2026-10-20T10:00:00Z", reason: "token_missing" }, agent: { ...none, lastAcceptedAt: "2026-10-20T10:00:00Z" } }))
    show()
    expect(within(await step("Webhook")).getByText("Connected")).toBeTruthy()
    expect(within(await step("Agent upload")).getByText("Connected")).toBeTruthy()
    expect(within(await step("Webhook")).queryByText(/no LiveKit token/)).toBeNull()
  })

  test("error: the last refused request is newer, with its fix", async () => {
    answer(connection({ webhook: { ...none, lastAcceptedAt: "2026-10-20T10:00:00Z", lastRejectedAt: "2026-10-20T10:01:00Z", reason: "token_missing" }, agent: { ...none, lastRejectedAt: "2026-10-20T10:01:00Z", reason: "grant_missing" } }))
    show()
    const webhook = await step("Webhook")
    expect(within(webhook).getByText("Error")).toBeTruthy()
    expect(within(webhook).getByText("Add the webhook URL in LiveKit, so LiveKit signs each webhook it sends.")).toBeTruthy()
    const agent = await step("Agent upload")
    expect(within(agent).getByText("Error")).toBeTruthy()
    expect(within(agent).getByText(/Upgrade livekit-agents to 1\.8\.6 or later/)).toBeTruthy()
  })

  test("key_mismatch names the key used and the key expected", async () => {
    answer(connection({ agent: { ...none, lastRejectedAt: "2026-10-20T10:01:00Z", reason: "key_mismatch", usedKey: "APIother123", expectedKey: "APIthis456" } }))
    show()
    const agent = await step("Agent upload")
    expect(within(agent).getByText("Error")).toBeTruthy()
    expect(within(agent).getByText("Used key APIother123, this project expects APIthis456")).toBeTruthy()
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
  expect(await screen.findByRole("button", { name: "Copy observability url" })).toBeTruthy()
  expect(screen.getByRole("button", { name: "Copy webhook url" })).toBeTruthy()
  expect(screen.getByLabelText("Observability URL").textContent).toBe(urls.observability)
  expect(screen.getByLabelText("Webhook URL").textContent).toBe(urls.webhook)
})

test("a null URL says VOICEGATEWAY_PUBLIC_URL is not set, and offers nothing to copy", async () => {
  answer(connection({ urls: { observability: null, webhook: null } }))
  show()
  await waitFor(() => expect(screen.getAllByText(/VOICEGATEWAY_PUBLIC_URL is not set on this instance/)).toHaveLength(2))
  expect(screen.queryByRole("button", { name: /^Copy/ })).toBeNull()
})

test("every reason has its own fix, and token_missing differs by source", () => {
  const reasons = ["token_missing", "key_mismatch", "signature_invalid", "grant_missing", "body_changed", "plan_inactive", "plan_cap", "too_large"] as const
  const texts = reasons.map((reason) => fixText("agent", { ...none, reason }).fix)
  expect(new Set(texts).size).toBe(reasons.length)
  expect(fixText("agent", { ...none, reason: "token_missing" }).fix).toMatch(/LIVEKIT_API_KEY and LIVEKIT_API_SECRET/)
  expect(fixText("webhook", { ...none, reason: "token_missing" }).fix).toMatch(/Add the webhook URL in LiveKit/)
  expect(fixText("agent", { ...none, reason: "plan_cap" }).fix).toMatch(/billing/)
  for (const reason of reasons) expect(`${fixText("agent", { ...none, reason }).title} ${fixText("agent", { ...none, reason }).fix}`).not.toMatch(/\u2014/)
})

test("equal accepted and refused instants stay waiting, not connected", () => {
  expect(sourceState({ ...none, lastAcceptedAt: "2026-10-20T10:00:00Z", lastRejectedAt: "2026-10-20T10:00:00Z", reason: "too_large" }).state).toBe("waiting")
})

test("polls every 5 s until the first call is stored, then stops", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const seen = answer(connection(), connection({ firstCall: { id: "8b0c3a52-6f7e-4d38-9a43-1c2d3e4f5a6b", startedAt: null } }))
  show()
  expect(within(await step("First call stored")).getByText("Waiting")).toBeTruthy()
  expect(seen.requests).toBe(1)
  await act(async () => vi.advanceTimersByTimeAsync(CONNECTION_POLL_MS))
  await waitFor(() => expect(within(screen.getByRole("listitem", { name: "First call stored" })).getByText("Connected")).toBeTruthy())
  expect(seen.requests).toBe(2)
  await act(async () => vi.advanceTimersByTimeAsync(CONNECTION_POLL_MS * 4))
  expect(seen.requests).toBe(2)
})

describe("on Overview", () => {
  const empty: CallPage = { items: [], page: { nextCursor: null, snapshot: "s" }, totals: totals([], 1500) }

  test("a project with no calls opens on the panel", async () => {
    answer(connection())
    server.use(http.get(`${project}/calls`, () => HttpResponse.json(empty)))
    show(OverviewPage)
    expect(await screen.findByRole("heading", { name: "Connect your agent" })).toBeTruthy()
  })

  test("a project with calls does not show it", async () => {
    answer(connection())
    show(OverviewPage)
    expect(await screen.findByText("Overview")).toBeTruthy()
    expect(screen.queryByRole("heading", { name: "Connect your agent" })).toBeNull()
  })
})
