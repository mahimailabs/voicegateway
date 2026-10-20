// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession } from "../src/lib/session.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"
import type { ProjectSettings } from "../src/lib/api-types"

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})
const notices = vi.hoisted(() => ({ saved: vi.fn(), error: vi.fn() }))
vi.mock("sonner", () => ({ toast: Object.assign(notices.saved, { error: notices.error }) }))
const { Thresholds } = await import("../src/features/settings/settings-dialog.tsx")
const initial: ProjectSettings = { thresholds: { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }, revision: 0, storageBucket: null, transcriptRetentionDays: null, traceRetentionDays: null, recordingRetentionDays: null, allowedOrigins: null }
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); setCurrentSession(null) })
function show(role = "member") {
  scopeRewrite.input({ url: new URL("http://localhost/org/default/project/p1/overview") })
  setCurrentSession({ email: "member@example.com", organisations: [{ id: "default", role }] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Thresholds /></QueryClientProvider>)
  return client
}

test("save waits for persistence and sends the displayed revision", async () => {
  let finish!: (response: Response) => void
  const writes: unknown[] = []
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.method === "PUT") { writes.push(await request.json()); return new Promise<Response>((resolve) => { finish = resolve }) }
    return json(200, initial)
  })
  const client = show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "2.5")
  await user.click(screen.getByRole("button", { name: "Save changes" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toEqual({ thresholds: { answerMs: 2500, joinMs: 2000, ttsStartMs: 1000 }, revision: 0 })
  expect((screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(true)
  expect(notices.saved).not.toHaveBeenCalled()
  finish(json(200, { ...initial, thresholds: { ...initial.thresholds, answerMs: 2500 }, revision: 1 }))
  await waitFor(() => expect(notices.saved).toHaveBeenCalledWith("Thresholds saved", expect.anything()))
  expect(client.getQueryData<ProjectSettings>(["settings", { org: "default", project: "p1" }])?.revision).toBe(1)
})

test("a conflicting save keeps the draft and exposes reload without reporting success", async () => {
  vi.stubGlobal("fetch", async (request: Request) => request.method === "PUT" ? json(409, { code: "revision_conflict", message: "Settings changed; reload before saving.", requestId: "r" }) : json(200, initial))
  show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "2.5")
  await user.click(screen.getByRole("button", { name: "Save changes" }))
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Settings changed"))
  expect(screen.getByRole("button", { name: "Reload settings" })).toBeTruthy()
  expect((answer as HTMLInputElement).value).toBe("2.5")
  expect(notices.saved).not.toHaveBeenCalled()
})

test("Viewer can read but cannot edit thresholds", async () => {
  const fetch = vi.fn(async () => json(200, initial)); vi.stubGlobal("fetch", fetch)
  show("viewer")
  expect((await screen.findByRole("spinbutton", { name: "Answer time" }) as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole("button", { name: "Reset to defaults" }) as HTMLButtonElement).disabled).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
})

test("Undo sends the saved revision and cannot overwrite a concurrent update", async () => {
  let stored = structuredClone(initial)
  const revisions: number[] = []
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.method !== "PUT") return json(200, stored)
    const body = await request.json()
    revisions.push(body.revision)
    if (body.revision !== stored.revision) return json(409, { code: "revision_conflict", message: "Settings changed; reload before saving.", requestId: "r" })
    stored = { ...stored, thresholds: body.thresholds, revision: stored.revision + 1 }
    return json(200, stored)
  })
  show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "2.5")
  await user.click(screen.getByRole("button", { name: "Save changes" }))
  await waitFor(() => expect(notices.saved).toHaveBeenCalled())
  const undo = notices.saved.mock.calls[0][1].action.onClick
  stored = { ...stored, thresholds: { ...stored.thresholds, answerMs: 3500 }, revision: 2 }
  undo()
  await waitFor(() => expect(notices.error).toHaveBeenCalled())
  expect(revisions).toEqual([0, 1])
  expect(stored.thresholds.answerMs).toBe(3500)
  expect(notices.saved).toHaveBeenCalledTimes(1)
})

test("values below the persisted lower bound cannot be submitted", async () => {
  const writes = vi.fn()
  vi.stubGlobal("fetch", async (request: Request) => { if (request.method === "PUT") writes(); return json(200, initial) })
  show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "0.01")
  expect((screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement).disabled).toBe(true)
  expect(writes).not.toHaveBeenCalled()
})

test("a refreshed server revision preserves the unsaved draft and its original revision", async () => {
  const writes: unknown[] = []
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.method === "PUT") { writes.push(await request.json()); return json(409, { code: "revision_conflict", message: "Settings changed", requestId: "r" }) }
    return json(200, initial)
  })
  const client = show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "2.5")
  client.setQueryData(["settings", { org: "default", project: "p1" }], { ...initial, revision: 1, thresholds: { ...initial.thresholds, answerMs: 3500 } })
  await user.click(screen.getByRole("button", { name: "Save changes" }))
  await screen.findByRole("alert")
  expect((answer as HTMLInputElement).value).toBe("2.5")
  expect(writes).toEqual([{ thresholds: { answerMs: 2500, joinMs: 2000, ttsStartMs: 1000 }, revision: 0 }])
})


test("a denied save cancels an in-flight refresh before clearing settings", async () => {
  let reads = 0
  let finish!: (response: Response) => void
  let signal: AbortSignal | undefined
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.method === "PUT") return json(403, { code: "forbidden", message: "Access revoked", requestId: "r" })
    if (!request.url.includes("/settings")) return json(401, { code: "unauthorized", message: "Session expired", requestId: "r" })
    if (++reads === 1) return json(200, initial)
    signal = request.signal
    return new Promise<Response>((resolve) => { finish = resolve })
  })
  const client = show()
  const user = userEvent.setup()
  const answer = await screen.findByRole("spinbutton", { name: "Answer time" })
  await user.clear(answer); await user.type(answer, "2.5")
  const key = ["settings", { org: "default", project: "p1" }]
  void client.refetchQueries({ queryKey: key, exact: true })
  await waitFor(() => expect(reads).toBe(2))
  await user.click(screen.getByRole("button", { name: "Save changes" }))
  await waitFor(() => expect(signal?.aborted).toBe(true))
  finish(json(200, initial))
  await waitFor(() => expect(client.getQueryData(key)).toBeUndefined())
  expect(screen.queryByRole("spinbutton", { name: "Answer time" })).toBeNull()
  expect(notices.saved).not.toHaveBeenCalled()
})
