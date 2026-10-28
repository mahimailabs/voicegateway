// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest"
import { TooltipProvider } from "../src/components/ui/tooltip"
import { handlers } from "../src/mocks/handlers"
import { resetAlerts } from "../src/mocks/settings"
import { setCurrentSession, setMockApi } from "../src/lib/session"

// The console's half of alert delivery (docs/specs/alerts-delivery.md): rule choices are saved on
// the server, not in this browser; a webhook's signing secret is shown once; a channel that stopped
// delivering says so on the rule that sends to it.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
  Element.prototype.getAnimations ??= () => []
})

const project = "*/api/v1/org/:org/project/:project"
const server = setupServer(...handlers)
const writes: { method: string; path: string; body: string }[] = []
server.events.on("request:start", async ({ request }) => {
  if (request.method !== "GET") writes.push({ method: request.method, path: new URL(request.url).pathname, body: await request.clone().text() })
})
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
  cleanup()
  resetAlerts()
  writes.length = 0
  setMockApi(false)
  setCurrentSession(null)
  localStorage.clear()
  server.resetHandlers()
})
afterAll(() => server.close())

function show(node: () => React.ReactNode) {
  setMockApi(true)
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = ["/alerts", "/calls/$callId"].map((path) => createRoute({ getParentRoute: () => root, path, component: node }))
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: ["/alerts"] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

test("turning a rule off saves it with updateAlertRule and keeps nothing in this browser", async () => {
  const { AlertsPage } = await import("../src/features/alerts/alerts-page")
  show(() => <AlertsPage />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("switch", { name: "Slow answers on" }))
  await waitFor(() => expect(writes).toContainEqual({ method: "PUT", path: "/api/v1/org/default/project/default-project/alert-rules/slow-answer", body: JSON.stringify({ on: false, to: [] }) }))
  expect(localStorage.length).toBe(0)
})

test("a channel that is not delivering is named on the rule that sends to it", async () => {
  server.use(
    http.get(`${project}/alert-setup`, () =>
      HttpResponse.json({
        items: ["slow-answer", "cut-off", "job-failed", "slow-join", "plain-phone", "recording-lost", "ingress-error"].map((ruleId) => ({ ruleId, on: true, to: ruleId === "job-failed" ? ["webhook"] : [] })),
        channels: [{ kind: "webhook", target: "hooks.example.com", recipients: null, broken: true, updatedAt: "2026-10-28T00:00:00Z", lastDelivery: null }],
        emailAvailable: false,
      }),
    ),
  )
  const { AlertsPage } = await import("../src/features/alerts/alerts-page")
  show(() => <AlertsPage />)
  expect(await screen.findByText("Not delivered to Webhook")).toBeTruthy()
  expect(screen.getAllByText("Not sending yet").length).toBe(6)
})

test("a new webhook's signing secret is shown once and never read back", async () => {
  setCurrentSession({ email: "ada@example.com", organisations: [{ id: "default", role: "admin" }] })
  const { AlertChannels } = await import("../src/features/settings/alert-channels")
  show(() => <AlertChannels isAdmin selfHosted />)
  const user = userEvent.setup()
  const form = await screen.findByRole("form", { name: "Webhook" })
  await user.type(within(form).getByLabelText("Endpoint URL"), "https://hooks.example.com/in/T0K3N")
  await user.click(within(form).getByRole("button", { name: "Add webhook" }))
  const secret = (await screen.findByLabelText("Signing secret")).textContent ?? ""
  expect(secret).toMatch(/^whsec_/)
  expect(await screen.findByText("hooks.example.com")).toBeTruthy()
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(document.body.textContent).not.toContain(secret))
  expect(document.body.textContent).not.toContain("T0K3N")
  await user.click(screen.getByRole("button", { name: /Send test/ }))
  expect(await screen.findByText(/Delivered/)).toBeTruthy()
})

test("a Viewer sees the channels but no form", async () => {
  const { AlertChannels } = await import("../src/features/settings/alert-channels")
  show(() => <AlertChannels isAdmin={false} selfHosted />)
  expect((await screen.findAllByText("Only an Admin sets up a channel.")).length).toBe(2)
  expect(screen.queryByRole("form", { name: "Webhook" })).toBeNull()
})
