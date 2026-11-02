// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest"
import { handlers, resetMockLiveKit } from "../src/mocks/handlers.ts"
import { capacity } from "../src/mocks/livekit.ts"
import { setCurrentSession, setMockApi } from "../src/lib/session.ts"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"
import { CapacityResult, CapacitySetupDialog } from "../src/features/agents/capacity.tsx"
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

function show(a: Agent) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <TooltipProvider>
        <CapacitySetupDialog agent={a} ms={idle} onOpenChange={() => {}} />
      </TooltipProvider>
    </QueryClientProvider>,
  )
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
