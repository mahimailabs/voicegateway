// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, useParams } from "@tanstack/react-router"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest"
import type { CallStats } from "../src/lib/api-types"
import { handlers } from "../src/mocks/handlers.ts"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"

// Acceptance checks 7 and 8 of phase 04e: each Overview section stands on its own read.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

// The globe draws with WebGL, which jsdom has not got.
vi.mock("../src/components/network-globe.tsx", () => ({ NetworkGlobe: () => <span data-testid="network-globe" /> }))

const { OverviewPage, NOT_SERVED, NOT_COMPUTED } = await import("../src/features/overview/overview.tsx")

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
})
afterAll(() => server.close())

const project = "*/api/v1/org/:org/project/:project"
const notFound = () => HttpResponse.json({ code: "not_found", message: "no such API path", requestId: "r" }, { status: 404 })

// getCallStats as web serves it in 04e: counts and answers, with what it does not compute null.
const served: CallStats = {
  from: "2026-10-01T00:00:00Z",
  to: "2026-10-08T00:00:00Z",
  complete: true,
  callCount: 2,
  byChannel: { phone: 1, web: 1, console: null, local: null, unknown: 0 },
  byDay: [{ date: "2026-10-07", callCount: 2 }],
  byHour: [{ hour: "2026-10-07T10:00:00Z", callCount: 2, answerP50Ms: 900 }],
  answers: { turns: 2, overBudget: 0, budgetMs: 1500, p50Ms: 900, p90Ms: 1100, values: [{ ms: 900, callId: "c-1", room: "room-one", turn: null, agentKey: "alpha" }], valuesTruncated: false },
  issueCounts: { high: null, medium: null, low: null, info: null },
  anatomyTurns: null,
  stages: null,
  chain: null,
  agents: [
    {
      key: "alpha",
      name: null,
      callCount: 2,
      answerTurns: 2,
      answerP50Ms: 900,
      issues: null,
      models: [],
      providers: null,
      anatomyTurns: null,
      stages: null,
      chain: null,
      calls: [
        { callId: "c-1", room: "room-one", answerP50Ms: 900 },
        { callId: "c-2", room: "room-two", answerP50Ms: 1100 },
      ],
      callsTruncated: false,
    },
  ],
}

// web as it is in 04e: the call stats and the 04d calls list served, the media server and SIP from
// the mock, and the five reads web does not serve yet answering 404.
function webIn04e(overrides: ReturnType<typeof http.get>[] = []) {
  server.use(
    ...overrides,
    http.get(`${project}/call-stats`, () => HttpResponse.json(served)),
    http.get(`${project}/agents`, notFound),
    http.get(`${project}/egress`, notFound),
    http.get(`${project}/ingress`, notFound),
    http.get(`${project}/workers`, notFound),
    http.get(`${project}/provider-probes`, notFound),
  )
}

function show(entry = "/") {
  const root = createRootRoute({ component: () => <Outlet /> })
  const CallOpened = () => <p>{`call ${(useParams({ strict: false }) as { callId?: string }).callId}`}</p>
  const routes = [
    createRoute({ getParentRoute: () => root, path: "/", component: OverviewPage }),
    createRoute({ getParentRoute: () => root, path: "/calls/$callId", component: CallOpened }),
    ...["/calls", "/agents", "/issues", "/sfu", "/sip", "/egress", "/ingress", "/project"].map((path) => createRoute({ getParentRoute: () => root, path, component: () => <p>{path}</p> })),
  ]
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [entry] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return router
}

test("the call figures and agent cards render while five reads are not served", async () => {
  webIn04e()
  show()
  expect(await screen.findByText("Overview")).toBeTruthy()
  // The five reads, plus the Live wire, which draws the provider probes too.
  await waitFor(() => expect(screen.getAllByText(new RegExp(NOT_SERVED)).length).toBe(6))
  for (const what of ["Agent workers", "Peak calls at once"]) expect(screen.getByText(`${what}: ${NOT_SERVED}`)).toBeTruthy()
  const tiles = screen.getByText("LiveKit project").closest("section")!
  for (const tile of ["Egress", "Ingress"]) expect(within(within(tiles).getByText(tile).closest("a")!).getByText(NOT_SERVED)).toBeTruthy()
  expect(within(screen.getByText("Model providers").closest("section")!).getByText(NOT_SERVED)).toBeTruthy()
  // The Live wire names the read it lacks.
  expect(within(screen.getByText("Live wire").closest("section")!).getByText(`Provider probes: ${NOT_SERVED}`)).toBeTruthy()
  expect(screen.getByText("0.90 s", { selector: "p" })).toBeTruthy()
  expect(screen.getByText("2 calls captured in the last 7 days", { exact: false })).toBeTruthy()
  expect(screen.getAllByText("Unnamed agent").length).toBeGreaterThan(0)
})

test("turn anatomy and the stage chain say they are not computed yet", async () => {
  webIn04e()
  show()
  expect(await screen.findByText(`Turn anatomy: ${NOT_COMPUTED}`)).toBeTruthy()
  await userEvent.setup().click(screen.getByText("Every stage, with where it comes from"))
  expect(await screen.findByText(`Every stage: ${NOT_COMPUTED}`)).toBeTruthy()
})

test("a read that fails with 500 shows its error, and the rest still render", async () => {
  webIn04e([http.get(`${project}/egress`, () => HttpResponse.json({ code: "internal_error", message: "egress read failed", requestId: "r" }, { status: 500 }))])
  show()
  expect(await screen.findByText("egress read failed")).toBeTruthy()
  expect(screen.getAllByText(new RegExp(NOT_SERVED)).length).toBe(5)
  expect(screen.getByText("0.90 s", { selector: "p" })).toBeTruthy()
})

test("a block in an agent's per-call strip opens that call", async () => {
  webIn04e()
  const router = show()
  await userEvent.setup().click(await screen.findByRole("button", { name: "Open the call in room-two" }))
  await waitFor(() => expect(router.state.location.pathname).toBe("/calls/c-2"))
  expect(await screen.findByText("call c-2")).toBeTruthy()
})

test("with the mock, every section is filled", async () => {
  show()
  expect(await screen.findByText("Overview")).toBeTruthy()
  await waitFor(() => expect(screen.queryAllByText("Loading").length).toBe(0))
  expect(screen.queryByText(new RegExp(NOT_SERVED))).toBeNull()
  expect(screen.queryByText(new RegExp(NOT_COMPUTED))).toBeNull()
  const project = screen.getByText("LiveKit project").closest("section")!
  for (const label of ["Media server", "Phone bridge", "Egress", "Ingress"]) expect(within(project).getByText(label)).toBeTruthy()
  expect(within(project).queryByText("Unknown")).toBeNull()
})

test("the globe is not built under the Settings modal, and is on the page without it", async () => {
  show("/?settings=thresholds")
  await screen.findByText("Model providers")
  await waitFor(() => expect(screen.queryAllByText("Loading").length).toBe(0))
  expect(screen.queryByTestId("network-globe")).toBeNull()
  cleanup()
  show()
  expect(await screen.findByTestId("network-globe")).toBeTruthy()
})
