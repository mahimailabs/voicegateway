// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router"
import { afterEach, expect, test, vi } from "vitest"
import { TooltipProvider } from "../src/components/ui/tooltip.tsx"
import { LivePage } from "../src/features/live/live-page.tsx"
import { WorkerMetrics } from "../src/features/agents/worker-metrics.tsx"
import { mediaServer } from "../src/mocks/livekit.ts"
import { ResourceMetrics } from "../src/components/resource-metrics.tsx"
import type { Resources, Workers } from "../src/lib/api-types"

vi.hoisted(() => {
  window.scrollTo = () => {}
  Element.prototype.getAnimations ??= () => []
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList
  globalThis.ResizeObserver ??= class { observe() {}; unobserve() {}; disconnect() {} } as unknown as typeof ResizeObserver
})
const clients: QueryClient[] = []
afterEach(() => { cleanup(); clients.forEach((c) => c.clear()); clients.length = 0; vi.unstubAllGlobals() })
function show(component: () => React.ReactNode) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const route = createRoute({ getParentRoute: () => root, path: "/", component })
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: ["/"] }) })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  render(<QueryClientProvider client={client}><TooltipProvider><RouterProvider router={router} /></TooltipProvider></QueryClientProvider>)
  return client
}
const answer = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })

test("historical rooms never replay on the current clock", async () => {
  vi.stubGlobal("fetch", async () => answer(mediaServer()))
  show(LivePage)
  expect(await screen.findByText("0 observed active · 0 in view")).toBeTruthy()
  expect(screen.queryByRole("button", { name: /playground-/ })).toBeNull()
})

test("a room appears once, updates from a new sample and freezes on source failure", async () => {
  const data = mediaServer()
  const now = Date.now()
  data.rooms = [{ ...data.rooms[0], name: "live-room", createdAt: new Date(now - 60000).toISOString(), lastSeenAt: new Date(now).toISOString() }]
  data.sourceStatus = { lastReadAt: new Date(now).toISOString(), lastGoodReadAt: new Date(now).toISOString(), errorClass: null }
  vi.stubGlobal("fetch", async () => answer(data))
  const client = show(LivePage)
  expect(await screen.findByText("1 observed active · 1 in view")).toBeTruthy()
  expect(screen.getAllByText("live-room")).toHaveLength(1)
  data.sourceStatus.errorClass = "unreachable"
  await client.invalidateQueries({ queryKey: ["media-server"] })
  expect(await screen.findByText("Source unavailable")).toBeTruthy()
  expect(await screen.findByText("0 observed active · 1 in view")).toBeTruthy()
  expect(screen.getByLabelText("Last observed")).toBeTruthy()
  data.sourceStatus = { lastReadAt: new Date(now + 1000).toISOString(), lastGoodReadAt: new Date(now + 1000).toISOString(), errorClass: null }
  await client.invalidateQueries({ queryKey: ["media-server"] })
  expect(await screen.findByLabelText("No longer listed")).toBeTruthy()
})

test("worker values keep missing distinct from zero and show partial endpoint failures", async () => {
  const t = new Date().toISOString()
  const good = { lastReadAt: t, lastGoodReadAt: t, errorClass: null }
  const failed = { ...good, errorClass: "unreachable" as const }
  const data: Workers = { source: "test", sourceStatus: good, endpoints: [{ id: "one", host: "host-one", sourceStatus: good }, { id: "two", host: "host-two", sourceStatus: failed }], workers: [
    { id: "one/a", nodename: "idle-worker", host: "host-one", loadThreshold: null, sourceStatus: good, samples: [{ t, load: 0, activeJobs: 0 }] },
    { id: "two/b", nodename: "unreachable-worker", host: "host-two", loadThreshold: null, sourceStatus: failed, samples: [{ t, load: null, activeJobs: null }] },
  ] }
  let denied = false
  vi.stubGlobal("fetch", async () => denied
    ? new Response(JSON.stringify({ code: "forbidden", message: "Access revoked", requestId: "r" }), { status: 403, headers: { "Content-Type": "application/json" } })
    : answer(data))
  const client = show(WorkerMetrics)
  const idle = (await screen.findByText("idle-worker")).closest("tr")!
  expect(within(idle).getByText("0%")).toBeTruthy()
  expect(within(idle).getByText("0")).toBeTruthy()
  const lost = screen.getByText("unreachable-worker").closest("tr")!
  expect(within(lost).getAllByText("Unknown")).toHaveLength(2)
  expect(within(lost).getByText("Last observed")).toBeTruthy()
  await waitFor(() => expect(screen.getByText("Source unavailable")).toBeTruthy())
  denied = true
  await client.invalidateQueries({ queryKey: ["workers"] })
  expect(await screen.findByText("Access revoked")).toBeTruthy()
  expect(screen.queryByText("idle-worker")).toBeNull()
})


test("container resources preserve zero, unknown and failed-source observations", async () => {
  const t = new Date().toISOString()
  const status = {lastReadAt:t,lastGoodReadAt:t,errorClass:null}
  const data: Resources = {source:"test",resources:[
    {id:"r1",kind:"worker",target:"measured-container",containerId:"/docker/a",host:"exporter",sourceStatus:status,samples:[{t,observedAt:t,cpuUsed:0,cpuCores:2,memoryBytes:1048576,memoryLimitBytes:null}]},
    {id:"r2",kind:"worker",target:"unknown-container",containerId:"/docker/b",host:"exporter",sourceStatus:{...status,errorClass:"unreachable"},samples:[{t,observedAt:t,cpuUsed:null,cpuCores:null,memoryBytes:null,memoryLimitBytes:null}]},
    {id:"r3",kind:"sfu",target:"separate-sfu",containerId:"/docker/c",host:"exporter",sourceStatus:status,samples:[]},
  ]}
  vi.stubGlobal("fetch", async () => answer(data))
  const client = show(() => <ResourceMetrics kind="worker" />)
  const measured = (await screen.findByText("measured-container")).closest("tr")!
  expect(within(measured).getByText("0.00 cores")).toBeTruthy()
  expect(within(measured).getByText("1.0 MiB")).toBeTruthy()
  const unknown = screen.getByText("unknown-container").closest("tr")!
  expect(within(unknown).getAllByText("Unknown")).toHaveLength(2)
  expect(within(unknown).getByText("Last observed")).toBeTruthy()
  expect(screen.queryByText("separate-sfu")).toBeNull()
  data.resources[0].sourceStatus.errorClass = "unreachable"
  await client.invalidateQueries({queryKey:["resources"]})
  await waitFor(() => expect(within(measured).getByText("Last observed")).toBeTruthy())
  expect(within(measured).getByText("1.0 MiB")).toBeTruthy()
  // A newly reachable exporter must not make an old measurement current.
  data.resources[0].sourceStatus.errorClass = null
  data.resources[0].samples[0].observedAt = new Date(Date.now() - 60000).toISOString()
  await client.invalidateQueries({ queryKey: ["resources"] })
  await waitFor(() => expect(within(measured).getByText("Last observed")).toBeTruthy())
  data.resources[0].samples[0].observedAt = new Date().toISOString()
  await client.invalidateQueries({ queryKey: ["resources"] })
  await waitFor(() => expect(within(measured).getByText("Current sample")).toBeTruthy())
})
