// @vitest-environment jsdom
import { createContext, useContext } from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest"
import { handlers, resetMockLiveKit } from "../src/mocks/handlers.ts"
import { capacity } from "../src/mocks/livekit.ts"
import { setCurrentSession, setMockApi } from "../src/lib/session.ts"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"
import { CapacityResult, CapacitySetupDialog, useCapacityAgent } from "../src/features/agents/capacity.tsx"
import type { Agent } from "../src/features/agents/agents-model.ts"
import type { MediaServerData } from "../src/features/media-server/room-drawer.tsx"

// The capacity dialog (docs/specs/capacity-runner.md): an Admin starts a run on the server, sees its
// steps and can stop it; an agent without a name is never offered a run.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
beforeEach(() => setMockApi(true))
afterEach(() => {
  cleanup()
  resetMockLiveKit()
  setMockApi(false)
  setCurrentSession(null)
})
afterAll(() => server.close())

const agent = (name: string | null): Agent => ({ key: name ?? "AG_1", name, cloud: null, jobs: [], regions: [], workers: [] })
const idle = { rooms: [], windows: [] } as unknown as MediaServerData

// The dialog keeps the run it watches in the URL, so it renders inside a router. remount() mounts
// it again on the same URL, as the agents page does when the agent leaves and rejoins the list.
function show(a: Agent) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: "/", component: () => <CapacitySetupDialog agent={a} ms={idle} onOpenChange={() => {}} /> })
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) })
  const tree = (key: number) => (
    <QueryClientProvider client={queries}>
      <TooltipProvider>
        <RouterProvider key={key} router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const { rerender } = render(tree(0))
  return { router, remount: () => rerender(tree(1)) }
}

// The agents page's own wiring: the agent under test comes from ?capacityAgent= and the live list,
// which drop() empties, as LiveKit does once a self-hosted agent's test rooms close.
const Listed = createContext<Agent[]>([])
function Page() {
  const [a, set] = useCapacityAgent(useContext(Listed))
  return <CapacitySetupDialog agent={a} ms={idle} onOpenChange={(o) => !o && set(null)} />
}
function showPage(a: Agent) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: "/", component: Page })
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [`/?capacityAgent=${encodeURIComponent(JSON.stringify(a.key))}`] }) })
  const tree = (list: Agent[]) => (
    <QueryClientProvider client={queries}>
      <TooltipProvider>
        <Listed.Provider value={list}>
          <RouterProvider router={router} />
        </Listed.Provider>
      </TooltipProvider>
    </QueryClientProvider>
  )
  const { rerender } = render(tree([a]))
  return { drop: () => rerender(tree([])) }
}

describe("capacity dialog", () => {
  test("Start needs consent, places the run on the server, and Stop ends it as stopped", async () => {
    const user = userEvent.setup()
    show(agent("vg-capture"))
    const start = await screen.findByRole("button", { name: /Start test/ })
    expect((start as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByRole("checkbox"))
    await user.click(start)
    const stop = await screen.findByRole("button", { name: "Stop test" })
    await user.click(stop)
    expect((await screen.findAllByText("stopped by a person")).length).toBeGreaterThan(0)
  }, 15000)

  test("a run that ended while the dialog remounted still shows its result", async () => {
    const user = userEvent.setup()
    const { router, remount } = show(agent("vg-capture"))
    const start = await screen.findByRole("button", { name: /Start test/ })
    await user.click(screen.getByRole("checkbox"))
    await user.click(start)
    await user.click(await screen.findByRole("button", { name: "Stop test" }))
    await screen.findAllByText("stopped by a person")
    expect(router.state.location.search).toHaveProperty("capacityRun")
    remount()
    expect((await screen.findAllByText("stopped by a person")).length).toBeGreaterThan(0)
    expect(screen.queryByRole("button", { name: /Start test/ })).toBeNull()
  }, 15000)

  test("the result stays when the agent leaves the list after Stop", async () => {
    const user = userEvent.setup()
    const { drop } = showPage(agent("vg-capture"))
    await user.click(await screen.findByRole("checkbox"))
    await user.click(screen.getByRole("button", { name: /Start test/ }))
    await user.click(await screen.findByRole("button", { name: "Stop test" }))
    await screen.findAllByText("stopped by a person")
    drop()
    expect(screen.getByRole("dialog")).toBeDefined()
    expect(screen.getAllByText("stopped by a person").length).toBeGreaterThan(0)
  }, 15000)

  test("an agent without a name cannot start a test", async () => {
    show(agent(null))
    const start = await screen.findByRole("button", { name: /Start test/ })
    expect((start as HTMLButtonElement).disabled).toBe(true)
    await userEvent.setup().click(screen.getByRole("checkbox"))
    expect((start as HTMLButtonElement).disabled).toBe(true)
  })

  test("a Viewer cannot start a test", async () => {
    setMockApi(false)
    setCurrentSession({ email: "viewer@example.com", organisations: [{ id: "default", role: "viewer" }] } as Parameters<typeof setCurrentSession>[0])
    show(agent("vg-capture"))
    const start = await screen.findByRole("button", { name: /Start test/ })
    await userEvent.setup().click(screen.getByRole("checkbox"))
    expect((start as HTMLButtonElement).disabled).toBe(true)
  })

  test("a run already open for the agent shows its progress, not the setup", async () => {
    await fetch("http://localhost/api/v1/org/default/project/default-project/capacity-runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent: "vg-capture", maxStep: 8, consent: true }) })
    show(agent("vg-capture"))
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop test" })).toBeDefined())
  })
})

describe("capacity result", () => {
  test("the captured run reads its capacity and says the tester limited it", () => {
    render(
      <TooltipProvider>
        <CapacityResult run={capacity()} />
      </TooltipProvider>,
    )
    expect(screen.getByText("8")).toBeDefined()
    expect(screen.getByText("limited by the tester")).toBeDefined()
  })
})
