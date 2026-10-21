// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from "vitest"
import { handlers } from "../src/mocks/handlers.ts"
import { workspaces as mockWorkspaces } from "../src/mocks/workspaces.ts"
import { setCurrentSession } from "../src/lib/session.ts"
import { setWorkspaces } from "../src/lib/workspaces.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"

// Who the console sends into the Cloud setup journey, and how Connect continues into it. The mock
// API answers; its switches (localStorage) make it a hosted install, an empty organisation, a project
// with no call. Every value is made up.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
  globalThis.IntersectionObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return []
    }
  } as unknown as typeof IntersectionObserver
})
// The globe draws with WebGL, which jsdom has not got.
vi.mock("../src/components/network-globe.tsx", () => ({ NetworkGlobe: () => null }))
const { OverviewPage } = await import("../src/features/overview/overview.tsx")
const { SetupPage } = await import("../src/features/setup/setup-page.tsx")

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  localStorage.clear()
  setCurrentSession(null)
})
afterAll(() => server.close())

function open(entry: string, { hosted = true, role = "admin", org = "default" }: { hosted?: boolean; role?: string; org?: string } = {}) {
  if (hosted) localStorage.setItem("vg.mock-entitlement", "hosted")
  localStorage.setItem("vg.mock-no-calls", "true")
  localStorage.setItem("vg.mock-empty-org", "true")
  setWorkspaces(mockWorkspaces().items)
  setCurrentSession({ email: "ada@example.com", organisations: [{ id: org, role }] })
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = [
    createRoute({ getParentRoute: () => root, path: "/overview", component: OverviewPage }),
    createRoute({ getParentRoute: () => root, path: "/setup", component: SetupPage }),
    ...["/calls", "/calls/$callId", "/agents", "/issues", "/sfu", "/sip", "/egress", "/ingress", "/project", "/settings"].map((path) => createRoute({ getParentRoute: () => root, path, component: () => <p>{path}</p> })),
  ]
  const router = createRouter({ rewrite: scopeRewrite, routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [entry] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return router
}

const agentHeading = () => screen.findByRole("heading", { level: 1, name: "Point your agent here." }, { timeout: 4000 })

describe("the Overview of a Cloud project with no call", () => {
  test("sends an Admin into setup", async () => {
    open("/org/default/project/default-project/overview")
    expect(await agentHeading()).toBeTruthy()
  })

  test("keeps Settings reachable: ?settings= opens over the console, not setup", async () => {
    const router = open("/org/default/project/default-project/overview?settings=connection")
    // Let the entitlement and connection reads answer before looking.
    await waitFor(() => expect(localStorage.getItem("vg.mock-entitlement")).toBe("hosted"))
    await new Promise((r) => setTimeout(r, 1000))
    expect(router.state.location.pathname).not.toBe("/setup")
    expect(screen.queryByRole("heading", { level: 1, name: "Point your agent here." })).toBeNull()
  })

  test.each([["viewer"], ["member"]])("leaves a %s in the console", async (role) => {
    const router = open("/org/default/project/default-project/overview", { role })
    await new Promise((r) => setTimeout(r, 1000))
    expect(router.state.location.pathname).not.toBe("/setup")
  })

  test("leaves a self-hosted install in the console", async () => {
    const router = open("/org/default/project/default-project/overview", { hosted: false })
    await new Promise((r) => setTimeout(r, 1000))
    expect(router.state.location.pathname).not.toBe("/setup")
  })
})

describe("Connect, then Continue", () => {
  test("opens the new project's Agent step inside the app, with no page load", async () => {
    const assign = vi.fn()
    // A full page load would go through location.assign; jsdom's cannot be spied on, so it is replaced.
    vi.stubGlobal("location", { ...window.location, assign, origin: "http://localhost" })
    open("/org/new-voice-team/project/none/setup?step=1", { org: "new-voice-team" })
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText("Paste your LiveKit keys"))
    await user.paste("LIVEKIT_URL=wss://example-project.livekit.cloud\nLIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample")
    await user.click(await screen.findByRole("button", { name: "Continue" }, { timeout: 4000 }))
    expect(await agentHeading()).toBeTruthy()
    expect(assign).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
