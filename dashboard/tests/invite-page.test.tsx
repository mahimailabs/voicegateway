// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"
import { seen } from "./fetch-call.ts"
import { EXISTING_ACCOUNT, INVALID, InvitePage, readInviteToken } from "../src/features/auth/invite-page.tsx"

// Acceptance check 17 of phase 06d, the accept screen: three of the seven cases.

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

const TOKEN = "A".repeat(43)
const json = (status: number, body: unknown) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

// web's two /auth/invitation routes, answering look and accept.
function serve(look: () => Response, accept: () => Response = () => json(204, null)) {
  const calls: { method: string; url: string; body: string | null; token: string | null }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const call = await seen(input, init)
      calls.push({ ...call, token: new Headers(init?.headers).get("X-Invitation-Token") })
      return call.method === "POST" ? accept() : look()
    }),
  )
  return calls
}

const pending = { kind: "invite", organisationName: "Acme", role: "Member", email: "new@example.com" }

test("the accept screen shows the organisation and role and one password field, and opens the console on 204", async () => {
  const calls = serve(() => json(200, pending))
  const onAccepted = vi.fn()
  render(<InvitePage token={TOKEN} onAccepted={onAccepted} />)
  expect(await screen.findByText("Join Acme")).toBeTruthy()
  expect(screen.getByText("As Member for new@example.com.")).toBeTruthy()
  expect(document.querySelectorAll("input[type=password]").length).toBe(1)
  expect(screen.getByText(new RegExp(EXISTING_ACCOUNT.replace(".", "\\.")))).toBeTruthy()
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("Password"), "twelve chars")
  await user.click(screen.getByRole("button", { name: "Join" }))
  await vi.waitFor(() => expect(onAccepted).toHaveBeenCalledOnce())
  expect(calls[0].token).toBe(TOKEN)
  expect(calls[0].url).toBe("/auth/invitation")
  const post = calls.find((c) => c.method === "POST")!
  expect(post.url).toBe("/auth/invitation/accept")
  expect(JSON.parse(post.body!)).toEqual({ token: TOKEN, password: "twelve chars" })
})

test("the token is read from the fragment and dropped from the address bar", () => {
  window.history.replaceState(null, "", "/invite#" + TOKEN)
  expect(readInviteToken()).toBe(TOKEN)
  expect(window.location.hash).toBe("")
  expect(window.location.href).not.toContain(TOKEN)
  expect(window.location.pathname).toBe("/invite")
})

test("invitation_invalid says the invitation is no longer valid", async () => {
  serve(() => json(404, { code: "invitation_invalid", message: "this invitation is no longer valid" }))
  render(<InvitePage token={TOKEN} onAccepted={() => {}} />)
  expect((await screen.findByRole("alert")).textContent).toBe(INVALID)
  expect(INVALID.startsWith("This invitation is no longer valid")).toBe(true)
  expect(document.querySelector("input[type=password]")).toBeNull()
})
