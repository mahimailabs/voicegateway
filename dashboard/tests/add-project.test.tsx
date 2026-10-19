// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { seen } from "./fetch-call.ts"
import { afterEach, expect, test, vi } from "vitest"
import { setCurrentSession } from "../src/lib/session.ts"

// Acceptance check 7 of phase 06f: an organisation's empty state when it has no project yet.

const { NoProjectYet } = await import("../src/components/no-project-yet.tsx")

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setCurrentSession(null)
})

// Made up for the test; it reaches no LiveKit server.
const SECRET = "test-secret-not-real"
const org = { id: "acme", name: "Acme" }
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

function serve(answer: () => Response = () => json(201, { id: "production", name: "Production" })) {
  const calls: { method: string; url: string; body: string | null }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      calls.push(await seen(input, init))
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
  await user.type(screen.getByLabelText("Name"), "Production")
  await user.type(screen.getByLabelText("LiveKit URL"), "wss://acme.livekit.cloud")
  await user.type(screen.getByLabelText("API key"), "APIacme")
  await user.type(screen.getByLabelText("API secret"), SECRET)
  return user
}

test("an Admin sees the Add project form, with the server command below it", () => {
  serve()
  signedInAs("admin")
  show()
  expect(screen.getByRole("form", { name: "Add project" })).toBeTruthy()
  expect((screen.getByLabelText("API secret") as HTMLInputElement).type).toBe("password")
  expect(screen.getByLabelText("Project add command").textContent).toContain('--org "acme"')
})

test("a Member sees that an admin must add a project, and no form", () => {
  const calls = serve()
  signedInAs("member")
  show()
  expect(screen.getByText("A project is one LiveKit project. An admin of Acme must add one.")).toBeTruthy()
  expect(screen.queryByRole("form", { name: "Add project" })).toBeNull()
  expect(screen.queryByLabelText("API secret")).toBeNull()
  expect(calls.length).toBe(0)
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

test("the API's error shows inline, and the secret is cleared", async () => {
  serve(() => json(409, { code: "project_exists", message: "this organisation already has a project with the id production; choose another name", requestId: "r" }))
  signedInAs("admin")
  const onCreated = show()
  const user = await fill()
  await user.click(screen.getByRole("button", { name: "Add project" }))
  expect((await screen.findByRole("alert")).textContent).toBe("this organisation already has a project with the id production; choose another name")
  expect(onCreated).not.toHaveBeenCalled()
  expect((screen.getByLabelText("API secret") as HTMLInputElement).value).toBe("")
  expect(document.body.textContent).not.toContain(SECRET)
})
