// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { seen } from "./fetch-call.ts"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession } from "../src/lib/session.ts"

// Acceptance check 7 of phase 06f: an organisation's empty state when it has no project yet.

// jsdom has no ResizeObserver; Radix's Switch measures its thumb with one.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

const { NoProjectYet } = await import("../src/components/no-project-yet.tsx")

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

// Made up for the test; it reaches no LiveKit server.
const SECRET = "test-secret-not-real"
const BUCKET_SECRET = "bucket-secret-not-real"
const org = { id: "acme", name: "Acme" }
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

// The install kind comes from the entitlement read: 404 is self-hosted, an answer is hosted, and
// "pending" never answers, as while it loads. Only the other requests are recorded.
type Install = "self-hosted" | "hosted" | "pending"
const entitlement = { from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z", limitMinutes: 2000, active: true, revision: 1, callSeconds: 60, unmeasuredCalls: 0, openCalls: 0, warningPercent: 0, ingestAllowed: true }
function serve(answer: () => Response = () => json(201, { id: "production", name: "Production" }), install: Install = "self-hosted") {
  const calls: { method: string; url: string; body: string | null }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const call = await seen(input, init)
      if (call.url.endsWith("/entitlement")) {
        if (install === "pending") return new Promise<Response>(() => {})
        return install === "hosted" ? json(200, entitlement) : json(404, { code: "not_found", message: "no entitlement API", requestId: "r" })
      }
      calls.push(call)
      return answer()
    }),
  )
  return calls
}

const signedInAs = (role: string) => setCurrentSession({ email: "ada@example.com", organisations: [{ id: "acme", role }] })
const show = (onCreated = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <NoProjectYet org={org} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return onCreated
}

async function fill() {
  const user = userEvent.setup()
  await user.clear(screen.getByLabelText("Name"))
  await user.type(screen.getByLabelText("Name"), "Production")
  await user.type(screen.getByLabelText("LiveKit URL"), "wss://acme.livekit.cloud")
  await user.type(screen.getByLabelText("API key"), "APIacme")
  await user.type(screen.getByLabelText("API secret"), SECRET)
  return user
}

test.each([["self-hosted"], ["hosted"], ["pending"]] as const)("on a %s install an Admin sees the Add project form and no self-hosted command or text", async (install) => {
  serve(undefined, install)
  signedInAs("admin")
  show()
  expect(screen.getByRole("form", { name: "Add project" })).toBeTruthy()
  expect((screen.getByLabelText("API secret") as HTMLInputElement).type).toBe("password")
  // Let any read settle (or stay pending) before looking.
  await new Promise((r) => setTimeout(r, 20))
  expect(document.body.textContent).not.toMatch(/docker|self-hosted|open-source|voicegateway project add/i)
})

test("the name starts as a generated readable name the user can edit", async () => {
  serve()
  signedInAs("admin")
  show()
  const name = screen.getByLabelText("Name") as HTMLInputElement
  expect(name.value).toMatch(/^[a-z]+(-[a-z]+)+$/)
  const user = userEvent.setup()
  await user.clear(name)
  await user.type(name, "Staging")
  expect(name.value).toBe("Staging")
})

// LiveKit Cloud's "Environment variables" block, with made-up values that reach no LiveKit.
const block = "LIVEKIT_URL=wss://example-project.livekit.cloud\nLIVEKIT_API_KEY=APIexample123\nLIVEKIT_API_SECRET=secretexample"
const value = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value

test("pasting the block into Paste from LiveKit fills URL, key and secret and leaves the box empty", async () => {
  const calls = serve()
  signedInAs("admin")
  const onCreated = show()
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("Paste from LiveKit"))
  await user.paste(block)
  expect(value("LiveKit URL")).toBe("wss://example-project.livekit.cloud")
  expect(value("API key")).toBe("APIexample123")
  expect(value("API secret")).toBe("secretexample")
  expect(value("Paste from LiveKit")).toBe("")
  expect(screen.getByText("Filled URL, key and secret from the pasted variables.")).toBeTruthy()
  // With the generated name, Add project is the only click left.
  const name = value("Name")
  await user.click(screen.getByRole("button", { name: "Add project" }))
  await waitFor(() => expect(onCreated).toHaveBeenCalled())
  const sent = JSON.parse(calls[0].body ?? "")
  expect(sent).toMatchObject({ livekitUrl: "wss://example-project.livekit.cloud", livekitApiKey: "APIexample123", livekitApiSecret: "secretexample" })
  expect(sent.name).toBe(name)
})

test("pasting the block into the API key input fills all three", async () => {
  serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("API key"))
  await user.paste(block)
  expect(value("LiveKit URL")).toBe("wss://example-project.livekit.cloud")
  expect(value("API key")).toBe("APIexample123")
  expect(value("API secret")).toBe("secretexample")
})

test("pasting a bare key into the API key input pastes it as usual", async () => {
  serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("API key"))
  await user.paste("APIexample123")
  expect(value("API key")).toBe("APIexample123")
  expect(value("LiveKit URL")).toBe("")
})

test("a paste that names none of the variables says so and keeps nothing in the box", async () => {
  serve()
  signedInAs("admin")
  show()
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("Paste from LiveKit"))
  await user.paste("hello")
  expect(value("Paste from LiveKit")).toBe("")
  expect(screen.getByText("No LIVEKIT_URL, LIVEKIT_API_KEY or LIVEKIT_API_SECRET in the pasted text.")).toBeTruthy()
})

test("a Member sees that an admin must add a project, and no form", () => {
  const calls = serve()
  signedInAs("member")
  show()
  expect(screen.getByText("A project is one LiveKit project. An admin of Acme must add one.")).toBeTruthy()
  expect(screen.queryByRole("form", { name: "Add project" })).toBeNull()
  expect(screen.queryByLabelText("API secret")).toBeNull()
  expect(calls.length).toBe(0)
  expect(screen.queryByLabelText("Project add command")).toBeNull()
})

test("a submit sends the fields to createProject, clears the secret and opens the new project", async () => {
  const calls = serve()
  signedInAs("admin")
  const onCreated = show()
  const user = await fill()
  await user.click(screen.getByRole("button", { name: "Add project" }))
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: "production", name: "Production" }))
  expect(calls).toHaveLength(1)
  expect(calls[0].method).toBe("POST")
  expect(calls[0].url).toBe("/api/v1/org/acme/projects")
  expect(JSON.parse(calls[0].body ?? "")).toEqual({ name: "Production", livekitUrl: "wss://acme.livekit.cloud", livekitApiKey: "APIacme", livekitApiSecret: SECRET })
  expect((screen.getByLabelText("API secret") as HTMLInputElement).value).toBe("")
})

test("an admin who brings a bucket sends it with the project and the bucket secret is cleared", async () => {
  const calls = serve()
  signedInAs("admin")
  const onCreated = show()
  const user = await fill()
  expect(screen.queryByLabelText("S3 endpoint")).toBeNull()
  await user.click(screen.getByLabelText("Keep recordings in my own S3 bucket"))
  await user.type(screen.getByLabelText("S3 endpoint"), "https://s3.us-east-1.amazonaws.com")
  await user.type(screen.getByLabelText("Bucket"), "acme-recordings")
  await user.type(screen.getByLabelText("Access key ID"), "AKIAACME")
  expect((screen.getByRole("button", { name: "Add project" }) as HTMLButtonElement).disabled).toBe(true)
  await user.type(screen.getByLabelText("Secret access key"), BUCKET_SECRET)
  await user.click(screen.getByRole("button", { name: "Add project" }))
  await waitFor(() => expect(onCreated).toHaveBeenCalled())
  expect(JSON.parse(calls[0].body ?? "").recordingBucket).toEqual({ endpoint: "https://s3.us-east-1.amazonaws.com", bucket: "acme-recordings", accessKeyId: "AKIAACME", secretAccessKey: BUCKET_SECRET })
  expect((screen.getByLabelText("Secret access key") as HTMLInputElement).value).toBe("")
})

test.each([
  [409, "project_exists", "this organisation already has a project with the id production; choose another name"],
  [422, "livekit_secret_invalid", "the LiveKit API secret did not verify for this key"],
  [422, "livekit_unreachable", "could not reach this LiveKit URL"],
  [422, "recording_bucket_refused", "the S3 endpoint refused a write or delete with this access key, secret and bucket name"],
  [422, "recording_bucket_unreachable", "could not reach this S3 endpoint"],
])("the API's %i %s shows inline, and the secret is cleared", async (status, code, message) => {
  serve(() => json(status, { code, message, requestId: "r" }))
  signedInAs("admin")
  const onCreated = show()
  const user = await fill()
  await user.click(screen.getByRole("button", { name: "Add project" }))
  expect((await screen.findByRole("alert")).textContent).toBe(message)
  expect(onCreated).not.toHaveBeenCalled()
  expect((screen.getByLabelText("API secret") as HTMLInputElement).value).toBe("")
  expect(document.body.textContent).not.toContain(SECRET)
})
