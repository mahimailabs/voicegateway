// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { seen } from "./fetch-call.ts"
import { afterEach, beforeAll, expect, test, vi } from "vitest"
import type { Workspace } from "../src/lib/api-types"
import { setCurrentSession } from "../src/lib/session.ts"
import { setWorkspaces } from "../src/lib/workspaces.ts"

// Acceptance check 9 of phase 06c: the API keys tab in Settings, Access. The sixth case comes from
// the 06c review: a failed create says so where the admin is looking.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})

const { ApiKeys } = await import("../src/features/settings/settings-dialog.tsx")

beforeAll(() => {
  setWorkspaces([{ id: "default", name: "Default", projects: [{ id: "p1", name: "Production" }] }] as unknown as Workspace[])
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

const SECRET = "vgk_abcdefghijkl_" + "A".repeat(43)
const json = (status: number, body: unknown) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
const key = { keyId: "abcdefghijkl", name: "ci", prefix: "vgk_abcdefghijkl", createdAt: "2026-10-18T00:00:00Z", createdBy: "ada@example.com", lastUsedAt: null, revokedAt: null }

// web as the console sees it, with the keys list answered by list.
function serve(list: () => Response = () => json(200, { items: [key] }), created: () => Response = () => json(201, { ...key, keyId: "mnopqrstuvwx", prefix: "vgk_mnopqrstuvwx", name: "new", secret: SECRET })) {
  const calls: { method: string; url: string }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const { method, url } = await seen(input, init)
      calls.push({ method, url })
      if (method === "POST") return created()
      if (method === "DELETE") return json(204, null)
      return list()
    }),
  )
  return calls
}

const signedInAs = (role: string) => setCurrentSession({ email: "ada@example.com", organisations: [{ id: "default", role }] })
const show = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{<ApiKeys />}</QueryClientProvider>)

test("an Admin sees each project's keys", async () => {
  const calls = serve()
  signedInAs("admin")
  show()
  expect(await screen.findByText("ci")).toBeTruthy()
  expect(screen.getByText("Production")).toBeTruthy()
  expect(screen.getByText(/vgk_abcdefghijkl · never used · by ada@example.com/)).toBeTruthy()
  expect(calls[0].url).toBe("/api/v1/org/default/project/p1/keys")
})

test("a new key's secret shows once, and closing the dialog takes it off the page", async () => {
  const calls = serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: /Create key/ }))
  await user.type(screen.getByLabelText("Key name"), "new")
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create key" }))
  expect((await screen.findByLabelText("New API key")).textContent).toBe(SECRET)
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/v1/org/default/project/p1/keys")).toBe(true)
  await user.click(screen.getByRole("button", { name: "Done" }))
  await waitFor(() => expect(screen.queryByText(SECRET)).toBeNull())
  expect(document.body.textContent).not.toContain(SECRET)
})

test("Revoke asks first, and only the confirmation calls revokeProjectKey", async () => {
  const calls = serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(await screen.findByLabelText("Revoke ci"))
  const dialog = await screen.findByRole("dialog")
  expect(calls.some((c) => c.method === "DELETE")).toBe(false)
  await user.click(within(dialog).getByRole("button", { name: "Revoke" }))
  await waitFor(() => expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/v1/org/default/project/p1/keys/abcdefghijkl"))
})

test("a Viewer sees that admins manage keys, with no list", () => {
  const calls = serve()
  signedInAs("viewer")
  show()
  expect(screen.getByText("Managed by admins")).toBeTruthy()
  expect(screen.queryByRole("button", { name: /Create key/ })).toBeNull()
  expect(calls.length).toBe(0)
})

test("a 404 says the keys are not served by this server yet", async () => {
  serve(() => json(404, { code: "not_found", message: "no such API path", requestId: "r" }))
  signedInAs("admin")
  show()
  expect(await screen.findByText("Not served by this server yet")).toBeTruthy()
})

test("a failed create shows its error inside the dialog", async () => {
  serve(undefined, () => json(500, { code: "internal_error", message: "the key could not be stored", requestId: "r" }))
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: /Create key/ }))
  await user.type(screen.getByLabelText("Key name"), "new")
  const dialog = screen.getByRole("dialog")
  await user.click(within(dialog).getByRole("button", { name: "Create key" }))
  expect((await within(dialog).findByRole("alert")).textContent).toBe("the key could not be stored")
})
