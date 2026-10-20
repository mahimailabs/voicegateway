import { cleanup, render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, vi } from "vitest"
import { TooltipProvider } from "../src/components/ui/tooltip"
import { handlers } from "../src/mocks/handlers"
import { capturedCalls } from "../src/mocks/fixtures"
import capture from "../src/mocks/capture-session.json"
import { setMockApi } from "../src/lib/session"

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
  window.scrollTo = () => {}
  Element.prototype.getAnimations ??= () => []
  Element.prototype.scrollIntoView ??= () => {}
})

export const calls = capturedCalls()
export const captured = calls.find((c) => c.room === capture.call.room)!
export const project = "*/api/v1/org/:org/project/:project"
export const server = setupServer(http.get("*/preview/recording.json", () => new HttpResponse(null, { status: 404 })), ...handlers)
const clients: QueryClient[] = []
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  setMockApi(false)
  localStorage.clear()
  server.resetHandlers()
})
afterAll(() => server.close())

/** Render the real Calls routes with isolated query state and optional deep-link filters. */
export async function showCalls(id?: string, search: Record<string, unknown> = {}) {
  setMockApi(true)
  const { CallsPage } = await import("../src/features/calls/calls-page")
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = ["/calls", "/calls/$callId"].map((path) => createRoute({ getParentRoute: () => root, path, component: CallsPage }))
  // Include the whole capture set rather than whichever calls fall in today's default range.
  const query = new URLSearchParams({ range: "1-2000000000", ...Object.fromEntries(Object.entries(search).map(([key, value]) => [key, JSON.stringify(value)])) })
  const entry = `/calls${id ? `/${encodeURIComponent(id)}` : ""}?${query}`
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [entry] }) })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  render(<QueryClientProvider client={client}><TooltipProvider><RouterProvider router={router} /></TooltipProvider></QueryClientProvider>)
  return router
}
