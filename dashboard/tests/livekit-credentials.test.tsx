// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { seen } from "./fetch-call.ts"
import { setCurrentSession } from "../src/lib/session.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"
import type { ProjectConnection } from "../src/lib/api-types"

// Settings > Connection: the project's LiveKit URL and key, and an Admin's "Change key and secret"
// (updateProjectLiveKitCredentials). Every value is made up and reaches no LiveKit.

vi.hoisted(() => {
  window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})
const { Connection, Organisation } = await import("../src/features/settings/settings-dialog.tsx")

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

const URL_ = "wss://example-project.livekit.cloud"
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
const waiting = { lastAcceptedAt: null, lastRejectedAt: null, reason: null, usedKey: null, expectedKey: null }
const connection = (apiKey: string): ProjectConnection => ({ livekit: { url: URL_, apiKey }, urls: { observability: "https://vg.example.com/ingest/default/p1", webhook: "https://vg.example.com/ingest/default/p1/livekit/webhook" }, webhook: waiting, agent: waiting, sdk: waiting, firstCall: null })

function serve(put: () => Response = () => new Response(null, { status: 204 }), entitlement: "self-hosted" | "hosted" = "self-hosted") {
  let key = "APIexample123"
  const calls: { method: string; url: string; body: string | null }[] = []
  vi.stubGlobal("fetch", async (input: Request) => {
    const call = await seen(input)
    calls.push(call)
    if (call.url.endsWith("/entitlement")) return entitlement === "hosted" ? json(200, { from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z", limitMinutes: 2000, active: true, revision: 1, callSeconds: 60, unmeasuredCalls: 0, openCalls: 0, warningPercent: 0, ingestAllowed: true }) : json(404, { code: "not_found", message: "no entitlement API", requestId: "r" })
    if (call.method === "PUT") {
      const answer = put()
      if (answer.status === 204) key = JSON.parse(call.body ?? "{}").livekitApiKey
      return answer
    }
    if (call.url.endsWith("/connection")) return json(200, connection(key))
    if (call.url.endsWith("/media-server")) return json(404, { code: "not_found", message: "none", requestId: "r" })
    return json(404, { code: "not_found", message: "not in this test", requestId: "r" })
  })
  return calls
}

function show(role: string, page: "connection" | "organisation" = "connection") {
  scopeRewrite.input({ url: new URL("http://localhost/org/default/project/p1/overview") })
  setCurrentSession({ email: "ada@example.com", organisations: [{ id: "default", role }] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}>{page === "connection" ? <Connection /> : <Organisation />}</QueryClientProvider>)
}

const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement

test("the LiveKit project's URL and current key show read-only", async () => {
  serve()
  show("member")
  expect(await screen.findByText(URL_)).toBeTruthy()
  expect(screen.getByText("APIexample123")).toBeTruthy()
})

test("a non-admin sees no Change key and secret form", async () => {
  const calls = serve()
  show("member")
  await screen.findByText("APIexample123")
  expect(screen.queryByRole("form", { name: "Change key and secret" })).toBeNull()
  expect(screen.queryByLabelText("New API secret")).toBeNull()
  expect(screen.getByText("Only an Admin changes the key and secret.")).toBeTruthy()
  expect(calls.some((c) => c.method === "PUT")).toBe(false)
})

test("an Admin's change sends the key and secret, clears the secret and reads the connection again", async () => {
  const calls = serve()
  show("admin")
  const user = userEvent.setup()
  await screen.findByRole("form", { name: "Change key and secret" })
  await user.type(input("New API key"), "APInewexample456")
  await user.type(input("New API secret"), "secretexample2")
  const reads = calls.filter((c) => c.url.endsWith("/connection")).length
  await user.click(screen.getByRole("button", { name: "Change key and secret" }))
  await waitFor(() => expect(screen.getByText("APInewexample456")).toBeTruthy())
  const put = calls.find((c) => c.method === "PUT")
  expect(put?.url).toBe("/api/v1/org/default/project/p1/livekit-credentials")
  expect(JSON.parse(put?.body ?? "")).toEqual({ livekitApiKey: "APInewexample456", livekitApiSecret: "secretexample2" })
  expect(calls.filter((c) => c.url.endsWith("/connection")).length).toBeGreaterThan(reads)
  expect(input("New API secret").value).toBe("")
  expect(screen.getByText("Key and secret changed.")).toBeTruthy()
  expect(document.body.textContent).not.toContain("secretexample2")
})

test("a 422 shows the API's message inline and the secret is cleared", async () => {
  serve(() => json(422, { code: "livekit_secret_invalid", message: "the LiveKit API secret did not verify for this key", requestId: "r" }))
  show("admin")
  const user = userEvent.setup()
  await screen.findByRole("form", { name: "Change key and secret" })
  await user.type(input("New API key"), "APInewexample456")
  await user.type(input("New API secret"), "secretexample2")
  await user.click(screen.getByRole("button", { name: "Change key and secret" }))
  expect((await screen.findByRole("alert")).textContent).toBe("the LiveKit API secret did not verify for this key")
  expect(input("New API secret").value).toBe("")
  expect(screen.getByText("APIexample123")).toBeTruthy()
})

test("a pasted block fills key and secret, and a different LiveKit URL is named", async () => {
  serve()
  show("admin")
  const user = userEvent.setup()
  await screen.findByRole("form", { name: "Change key and secret" })
  await user.click(screen.getByLabelText("Paste from LiveKit"))
  await user.paste("LIVEKIT_URL=wss://other-project.livekit.cloud\nLIVEKIT_API_KEY=APInewexample456\nLIVEKIT_API_SECRET=secretexample2")
  expect(input("New API key").value).toBe("APInewexample456")
  expect(input("New API secret").value).toBe("secretexample2")
  expect(input("Paste from LiveKit").value).toBe("")
  expect(screen.getByText(`Filled key and secret from the pasted variables. This project is ${URL_}; a different LiveKit project needs its own VoiceGateway project.`)).toBeTruthy()
})

test("the same block pasted into the key input fills both, and the same URL is not flagged", async () => {
  serve()
  show("admin")
  const user = userEvent.setup()
  await screen.findByRole("form", { name: "Change key and secret" })
  await user.click(input("New API key"))
  await user.paste(`LIVEKIT_URL=${URL_}/\nLIVEKIT_API_KEY=APInewexample456\nLIVEKIT_API_SECRET=secretexample2`)
  expect(input("New API key").value).toBe("APInewexample456")
  expect(input("New API secret").value).toBe("secretexample2")
  expect(screen.getByText("Filled key and secret from the pasted variables.")).toBeTruthy()
})

test.each([
  ["self-hosted", true],
  ["hosted", false],
] as const)("Organisation on a %s install says the open-source terms: %s", async (install, said) => {
  serve(undefined, install)
  show("admin", "organisation")
  if (said) await screen.findByText(/open-source edition is one organisation/)
  else {
    // The hosted answer has arrived once its usage shows; the open-source text is still absent.
    await screen.findByText("Profiled call usage")
    expect(screen.queryByText(/open-source edition is one organisation/)).toBeNull()
  }
  expect(screen.getByText(/^The organisation that owns your projects\./)).toBeTruthy()
})
