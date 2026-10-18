// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router"
import { afterEach, beforeAll, expect, test, vi } from "vitest"
import { seen } from "./fetch-call.ts"
import type { Workspace } from "../src/lib/api-types"
import { setCurrentSession } from "../src/lib/session.ts"
import { setWorkspaces } from "../src/lib/workspaces.ts"
import { createFilterQuery, createFilterRule } from "../src/components/reui/filters/filters-query.tsx"

// Acceptance check 17 of phase 06d, Settings, People: four of the seven cases.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})

const { People } = await import("../src/features/settings/settings-dialog.tsx")

beforeAll(() => {
  setWorkspaces([{ id: "default", name: "Default", projects: [] }] as unknown as Workspace[])
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.scrollIntoView ??= () => undefined
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

const LINK = "https://vg.example/invite#" + "B".repeat(43)
const json = (status: number, body: unknown) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
const at = "2026-10-18T12:00:00Z"
const later = "2026-10-25T12:00:00Z"
const row = (id: string, email: string, state: string) => ({ id, kind: "invite", email, role: "Member", state, createdAt: at, expiresAt: later, invitedBy: "ada@example.com" })
const list = { items: [row("i-1", "pending@example.com", "pending"), row("i-2", "accepted@example.com", "accepted"), row("i-3", "revoked@example.com", "revoked")] }
const members = { items: [{ userId: "u-ada", email: "ada@example.com", role: "Admin" }, { userId: "u-bob", email: "bob@example.com", role: "Member" }] }

// web as the console sees it, with createInvitation answering created.
function serve(created: () => Response = () => json(201, { invitation: row("i-9", "new@example.com", "pending"), link: LINK, emailed: false })) {
  const calls: { method: string; url: string; body: string | null }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const call = await seen(input, init)
      calls.push(call)
      if (call.method === "POST" && call.url.endsWith("/invitations")) return created()
      if (call.method === "DELETE") return json(204, null)
      if (call.url.endsWith("/members")) return json(200, members)
      if (call.url.endsWith("/invitations")) return json(200, list)
      return json(404, { code: "not_found", message: "no such API path", requestId: "r" })
    }),
  )
  return calls
}

const signedInAs = (role: string) => setCurrentSession({ email: "ada@example.com", organisations: [{ id: "default", role }] })

// People in Settings, under a router, since the list keeps its filter and sort in the URL.
function show() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: "/", component: People })]), history: createMemoryHistory({ initialEntries: ["/"] }) })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

async function sendInvite(email: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("Email to invite"), email)
  await user.click(screen.getByRole("button", { name: /Send invite/ }))
  return user
}

test("an invite without SMTP shows the link once; with SMTP it shows no link, and after a failed send a Resend button", async () => {
  serve()
  signedInAs("admin")
  show()
  const user = await sendInvite("new@example.com")
  expect((await screen.findByLabelText("Invitation link")).textContent).toBe(LINK)
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(document.body.textContent).not.toContain(LINK))
  cleanup()

  serve(() => json(201, { invitation: row("i-9", "new@example.com", "pending"), link: null, emailed: true }))
  show()
  await sendInvite("new@example.com")
  expect(await screen.findByText("Emailed to new@example.com.")).toBeTruthy()
  expect(screen.queryByLabelText("Invitation link")).toBeNull()
  cleanup()

  const calls = serve(() => json(201, { invitation: row("i-9", "new@example.com", "pending"), link: null, emailed: false }))
  show()
  const again = await sendInvite("new@example.com")
  expect((await screen.findByText("Could not send the email to new@example.com.")).getAttribute("role")).toBe("alert")
  expect(screen.queryByLabelText("Invitation link")).toBeNull()
  await again.click(screen.getByRole("button", { name: "Resend" }))
  await vi.waitFor(() => expect(calls.filter((c) => c.method === "POST").length).toBe(2))
  expect(JSON.parse(calls.filter((c) => c.method === "POST")[1].body!)).toEqual({ email: "new@example.com", role: "Member" })
}, 20_000) // three renders and three typed invites; near 5 s under a loaded machine

test("the invitations list filters by state through the URL", async () => {
  serve()
  signedInAs("admin")
  const router = show()
  expect(await screen.findByText("pending@example.com")).toBeTruthy()
  expect(screen.getByText("accepted@example.com")).toBeTruthy()
  const q = createFilterQuery([createFilterRule({ id: "r1", path: ["state"], operator: "is", value: "accepted" })])
  await router.navigate({ to: "/", search: { "invites.q": q } as never })
  await waitFor(() => expect(screen.queryByText("pending@example.com")).toBeNull())
  expect(screen.getByText("accepted@example.com")).toBeTruthy()
  expect(screen.queryByText("revoked@example.com")).toBeNull()
  expect((router.state.location.search as Record<string, unknown>)["invites.q"]).toEqual(q)
})

test("Revoke asks first, and only the confirmation calls revokeInvitation", async () => {
  const calls = serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Revoke pending@example.com"))
  const dialog = await screen.findByRole("dialog")
  expect(calls.some((c) => c.method === "DELETE")).toBe(false)
  await user.click(within(dialog).getByRole("button", { name: "Revoke" }))
  await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/v1/org/default/invitations/i-1"))
})

test("a Viewer sees no Invite form and no reset action", async () => {
  const calls = serve()
  signedInAs("viewer")
  show()
  expect(await screen.findByText("bob@example.com")).toBeTruthy()
  expect(screen.queryByRole("form", { name: "Invite people" })).toBeNull()
  expect(screen.queryByLabelText(/^Actions for /)).toBeNull()
  expect(screen.queryByText("Password reset")).toBeNull()
  expect(calls.some((c) => c.url.endsWith("/invitations"))).toBe(false)
})
