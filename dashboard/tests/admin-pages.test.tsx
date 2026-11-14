// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { afterEach, beforeAll, expect, test, vi } from "vitest"
import { seen } from "./fetch-call.ts"
import { type Session, setCurrentSession } from "../src/lib/session.ts"

// docs/specs/instance-admin.md: the instance lists (in Settings) and an operator page show for an
// instance admin only, read nothing for anyone else, and an operator page loads only a path the
// session names.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})

const { AdminFramedPage, InstanceOrganisations, InstanceUsers } = await import("../src/features/admin/admin-pages.tsx")

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.scrollIntoView ??= () => undefined
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
const at = "2026-11-01T12:00:00Z"
const organisations = {
  total: 2,
  items: [
    { id: "acme", name: "Acme Support", createdAt: at, members: 4, admins: 1, projects: 2 },
    { id: "default", name: "Default", createdAt: at, members: 1, admins: 1, projects: 0 },
  ],
}
const users = {
  total: 2,
  items: [
    { id: "00000000-0000-4000-8000-000000000001", email: "kai@example.com", createdAt: at, instanceAdmin: true, organisations: [{ id: "default", role: "Admin" }] },
    { id: "00000000-0000-4000-8000-000000000002", email: "john.doe@example.com", createdAt: at, instanceAdmin: false, organisations: [] },
  ],
}
const usage = { organisationId: "acme", from: at, to: at, callSeconds: 7265, calls: 31, inferredCalls: 2, inferredCallSeconds: 120, unendedCalls: 1, unmeasuredCalls: 0 }

function serve() {
  const calls: { method: string; url: string }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const call = await seen(input, init)
      calls.push(call)
      if (call.url.startsWith("/api/v1/admin/organisations/acme/usage")) return json(200, usage)
      if (call.url === "/api/v1/admin/organisations") return json(200, organisations)
      if (call.url === "/api/v1/admin/users") return json(200, users)
      return json(404, { code: "not_found", message: "no such API path", requestId: "r" })
    }),
  )
  return calls
}

const admin: Session = { email: "kai@example.com", organisations: [{ id: "default", role: "admin" }], instanceAdmin: true, adminPages: [{ label: "Console", path: "/business/" }] }
const member: Session = { email: "john.doe@example.com", organisations: [{ id: "default", role: "admin" }], instanceAdmin: false, adminPages: [] }

function show(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const routes = [
    createRoute({ getParentRoute: () => root, path: "/organisations", component: InstanceOrganisations }),
    createRoute({ getParentRoute: () => root, path: "/users", component: InstanceUsers }),
    createRoute({ getParentRoute: () => root, path: "/admin/page/$slug", component: AdminFramedPage }),
  ]
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

test("an instance admin lists organisations, and a row opens that organisation's call time", async () => {
  const calls = serve()
  setCurrentSession(admin)
  show("/organisations")
  expect(await screen.findByText("Acme Support")).toBeTruthy()
  expect(screen.getByText("Default")).toBeTruthy()
  expect(calls.some((c) => c.url.includes("/usage"))).toBe(false)
  await userEvent.setup().click(screen.getByText("Acme Support"))
  // 7265 seconds is 121 whole minutes: summed, then rounded down once.
  expect(await screen.findByText("121")).toBeTruthy()
  expect(screen.getByText("31")).toBeTruthy()
  expect(calls.filter((c) => c.url.includes("/usage")).length).toBe(1)
})

test("an instance admin lists accounts with their roles and the admin mark", async () => {
  serve()
  setCurrentSession(admin)
  show("/users")
  expect(await screen.findByText("kai@example.com")).toBeTruthy()
  expect(screen.getAllByText("Instance admin").length).toBe(1) // Kai's mark
  expect(screen.getByText("None")).toBeTruthy()
})

test("an account without the flag sees no admin page and the console asks the API nothing", async () => {
  const calls = serve()
  setCurrentSession(member)
  for (const path of ["/organisations", "/users", "/admin/page/1"]) {
    show(path)
    expect(await screen.findByText("No such page")).toBeTruthy()
    expect(document.querySelector("iframe")).toBeNull()
    cleanup()
  }
  expect(calls).toEqual([])
})

test("an operator page frames only the path the session names", async () => {
  serve()
  setCurrentSession(admin)
  show("/admin/page/1")
  await waitFor(() => expect(document.querySelector("iframe")?.getAttribute("src")).toBe("/business/"))
  expect(screen.getByRole("heading", { name: "Console" })).toBeTruthy()
  cleanup()

  show("/admin/page/2")
  expect(await screen.findByText("No such page")).toBeTruthy()
  cleanup()

  setCurrentSession({ ...admin, adminPages: [{ label: "Elsewhere", path: "//evil.example/" }] })
  show("/admin/page/1")
  expect(await screen.findByText("No such page")).toBeTruthy()
  expect(document.querySelector("iframe")).toBeNull()
})

test("Settings shows its Instance group to an instance admin only", async () => {
  const { SettingsDialog } = await import("../src/features/settings/settings-dialog.tsx")
  const { scopeRewrite } = await import("../src/lib/url-state.ts")
  const { TooltipProvider } = await import("../src/components/ui/tooltip.tsx")
  let calls: { url: string }[] = []
  const open = (session: Session, page: string) => {
    calls = serve()
    scopeRewrite.input({ url: new URL("http://localhost/org/default/project/p1/overview") })
    setCurrentSession(session)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TooltipProvider>
          <SettingsDialog open onOpenChange={() => {}} page={page} onPageChange={() => {}} />
        </TooltipProvider>
      </QueryClientProvider>,
    )
  }
  open(admin, "account")
  expect(await screen.findByText("Instance")).toBeTruthy()
  expect(screen.getByRole("button", { name: "Organisations" })).toBeTruthy()
  expect(screen.getByRole("button", { name: "Users" })).toBeTruthy()
  cleanup()

  // An address that names an Instance page opens the first page for everyone else.
  open(member, "instance-users")
  await screen.findByText("Settings")
  expect(screen.queryByText("Instance")).toBeNull()
  expect(screen.queryByRole("button", { name: "Users" })).toBeNull()
  expect(calls.some((c) => c.url.includes("/admin/"))).toBe(false)
})
