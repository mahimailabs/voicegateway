// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession } from "../src/lib/session.ts"

vi.hoisted(() => {
 window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})
const notices = vi.hoisted(() => ({ saved: vi.fn(), error: vi.fn() }))
vi.mock("sonner", () => ({ toast: Object.assign(notices.saved, { error: notices.error }) }))
const { Account } = await import("../src/features/settings/settings-dialog.tsx")
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); setCurrentSession(null) })
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
function show() {
 setCurrentSession({ email: "user@example.com", organisations: [{ id: "default", role: "viewer" }] })
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
 render(<QueryClientProvider client={client}><Account /></QueryClientProvider>)
 return client
}

test("profile Save waits for persistence and sends only the name and read revision", async () => {
 let finish!: (response: Response) => void
 const writes: unknown[] = []
 vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
  if (init.method === "PUT") { writes.push(JSON.parse(init.body as string)); return new Promise<Response>((resolve) => { finish = resolve }) }
  return json(200, { name: "Original", revision: 3 })
 })
 const client = show()
 const user = userEvent.setup()
 const input = await screen.findByRole("textbox", { name: "Name" })
 await user.clear(input); await user.type(input, "New name")
 await user.click(screen.getByRole("button", { name: "Save", exact: true }))
 await waitFor(() => expect(writes).toEqual([{ name: "New name", revision: 3 }]))
 expect(notices.saved).not.toHaveBeenCalled()
 expect((screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(true)
 finish(json(200, { name: "New name", revision: 4 }))
 await waitFor(() => expect(notices.saved).toHaveBeenCalledWith("Profile saved"))
 expect(client.getQueryData(["account-profile", "user@example.com"])).toEqual({ name: "New name", revision: 4 })
})

test("profile conflict keeps the draft until explicit reload", async () => {
 let current = { name: "Original", revision: 0 }
 vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => init.method === "PUT" ? json(409, { message: "Profile changed; reload before saving" }) : json(200, current))
 show()
 const user = userEvent.setup()
 const input = await screen.findByRole("textbox", { name: "Name" })
 await user.clear(input); await user.type(input, "Unsaved draft")
 current = { name: "Newer stored name", revision: 1 }
 await user.click(screen.getByRole("button", { name: "Save", exact: true }))
 await screen.findByRole("alert")
 expect((input as HTMLInputElement).value).toBe("Unsaved draft")
 expect(notices.saved).not.toHaveBeenCalled()
 await user.click(screen.getByRole("button", { name: "Reload profile" }))
 await waitFor(() => expect((input as HTMLInputElement).value).toBe("Newer stored name"))
})


test("denied profile save cancels an outstanding read before clearing cached identity data", async () => {
 let reads = 0
 let finish!: (response: Response) => void
 vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
  if (init.method === "PUT") return json(401, { message: "Session expired" })
  reads += 1
  if (reads > 1) return new Promise<Response>((resolve) => { finish = resolve })
  return json(200, { name: "Original", revision: 0 })
 })
 const client = show()
 const user = userEvent.setup()
 const input = await screen.findByRole("textbox", { name: "Name" })
 await user.clear(input); await user.type(input, "Draft")
 void client.invalidateQueries({ queryKey: ["account-profile", "user@example.com"] })
 await waitFor(() => expect(reads).toBe(2))
 await user.click(screen.getByRole("button", { name: "Save", exact: true }))
 await screen.findByRole("button", { name: "Retry profile" })
 finish(json(200, { name: "Stale private name", revision: 1 }))
 await waitFor(() => expect(client.getQueryData(["account-profile", "user@example.com"])).toBeUndefined())
 expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull()
 expect(notices.saved).not.toHaveBeenCalled()
})

test("an account with two Auth0 connections shows one Auth0 badge", async () => {
 vi.stubGlobal("fetch", async () => json(200, { name: "Ada", revision: 1, hasPassword: false, identities: [
  { provider: "auth0", linkedAt: "2026-10-23T20:00:00Z" }, { provider: "auth0", linkedAt: "2026-10-23T20:05:00Z" }, { provider: "google", linkedAt: "2026-10-23T20:10:00Z" },
 ] }))
 show()
 await screen.findByText("Google")
 expect(screen.getAllByText("Auth0")).toHaveLength(1)
 expect(screen.getByText("No password set")).toBeTruthy()
})
