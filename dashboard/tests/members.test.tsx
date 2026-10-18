// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { seen } from "./fetch-call.ts"
import { afterEach, beforeAll, expect, test, vi } from "vitest"

// jsdom has no matchMedia, which the settings module's layout hooks read when it loads.
vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})
import { Account, LAST_ADMIN, People } from "../src/features/settings/settings-dialog.tsx"
import { setCurrentSession } from "../src/lib/session.ts"

// Acceptance check 9 of phase 06b: the Members list in Settings, by the signed-in user's role.
// The sixth case comes from the 06b review: your own role change re-reads the session.

beforeAll(() => {
  // Radix Select measures and captures the pointer, which jsdom does not implement.
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  Element.prototype.scrollIntoView ??= () => undefined
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

const members = {
  items: [
    { userId: "u-ada", email: "ada@example.com", role: "Admin" },
    { userId: "u-bob", email: "bob@example.com", role: "Member" },
    { userId: "u-cat", email: "cat@example.com", role: "Viewer" },
  ],
}

const json = (status: number, body: unknown) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

// web as the console sees it: the member list, pending invitations, and an answer for a member write.
function serve(write: () => Response = () => json(200, members.items[1])) {
  const calls: { method: string; url: string; body: string | null }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const call = await seen(input, init)
      const { method, url } = call
      calls.push(call)
      if (method !== "GET") return write()
      if (url === "/auth/session") return json(200, session)
      if (url.endsWith("/members")) return json(200, members)
      if (url.endsWith("/invitations")) return json(200, { items: [] })
      return json(404, { code: "not_found", message: "no such API path", requestId: "r" })
    }),
  )
  return calls
}

// What /auth/session answers when the console reads it again.
let session = { email: "ada@example.com", organisations: [{ id: "default", role: "admin" }] }

function signedInAs(role: string) {
  setCurrentSession({ email: "ada@example.com", organisations: [{ id: "default", role }] })
}

const show = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)

test("an Admin sees every member, with a role picker on each and Remove on all but their own", async () => {
  serve()
  signedInAs("admin")
  show(<People />)
  expect(await screen.findByText("ada@example.com (you)")).toBeTruthy()
  for (const email of ["ada@example.com", "bob@example.com", "cat@example.com"]) expect(screen.getByLabelText(`Role of ${email}`)).toBeTruthy()
  expect(screen.queryByLabelText("Remove ada@example.com")).toBeNull()
  expect(screen.getByLabelText("Remove bob@example.com")).toBeTruthy()
  expect(screen.getByLabelText("Remove cat@example.com")).toBeTruthy()
})

test("changing a role calls updateMember", async () => {
  const calls = serve()
  signedInAs("admin")
  show(<People />)
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Role of bob@example.com"))
  await user.click(within(await screen.findByRole("listbox")).getByRole("option", { name: "Viewer" }))
  await vi.waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true))
  const patch = calls.find((c) => c.method === "PATCH")!
  expect(patch.url).toBe("/api/v1/org/default/members/u-bob")
  expect(JSON.parse(patch.body!)).toEqual({ role: "Viewer" })
})

test("a last_admin answer says an organisation needs an admin", async () => {
  serve(() => json(409, { code: "last_admin", message: "an organisation needs at least one admin", requestId: "r" }))
  signedInAs("admin")
  show(<People />)
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Role of ada@example.com"))
  await user.click(within(await screen.findByRole("listbox")).getByRole("option", { name: "Viewer" }))
  expect((await screen.findByRole("alert")).textContent).toBe(LAST_ADMIN)
})

test("a Viewer sees the list with no role picker, no Remove and no Invite form", async () => {
  serve()
  signedInAs("viewer")
  show(<People />)
  const bob = (await screen.findByText("bob@example.com")).closest("[data-slot=item]")
  expect(bob?.querySelector("[data-slot=item-actions]")?.textContent).toBe("Member")
  expect(screen.queryByLabelText(/^Role of /)).toBeNull()
  expect(screen.queryByLabelText(/^Remove /)).toBeNull()
  expect(screen.queryByRole("form", { name: "Invite people" })).toBeNull()
})

test("the Role row shows the signed-in user's role", () => {
  signedInAs("member")
  show(<Account />)
  expect(screen.getByText("Role").closest("[data-slot=item]")?.textContent).toContain("Member")
})

test("demoting yourself reads the session again and hides the admin controls", async () => {
  session = { email: "ada@example.com", organisations: [{ id: "default", role: "member" }] }
  const calls = serve(() => json(200, { userId: "u-ada", email: "ada@example.com", role: "Member" }))
  signedInAs("admin")
  show(<People />)
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Role of ada@example.com"))
  await user.click(within(await screen.findByRole("listbox")).getByRole("option", { name: "Member" }))
  await vi.waitFor(() => expect(screen.queryByLabelText(/^Role of /)).toBeNull())
  expect(calls.some((c) => c.url === "/auth/session")).toBe(true)
  expect(screen.queryByRole("form", { name: "Invite people" })).toBeNull()
})

test("Remove asks first, and only the confirmation calls removeMember", async () => {
  const calls = serve(() => json(204, null))
  signedInAs("admin")
  show(<People />)
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Remove bob@example.com"))
  const dialog = await screen.findByRole("dialog")
  expect(within(dialog).getByText("Remove bob@example.com?")).toBeTruthy()
  expect(calls.some((c) => c.method === "DELETE")).toBe(false)
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
  expect(calls.some((c) => c.method === "DELETE")).toBe(false)
  await user.click(screen.getByLabelText("Remove bob@example.com"))
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }))
  await vi.waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/v1/org/default/members/u-bob"))
})
