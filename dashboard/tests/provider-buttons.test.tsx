// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { SignInScreen } from "../src/features/auth/sign-in-screen.tsx"
import { PROVIDER_REFUSED } from "../src/lib/providers.ts"

// Acceptance check 9 of phase 06h: the sign-in screen shows a button for each provider web has on,
// above the email form, and none for one that is off.
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/")
})

const providers = (on: { google: boolean; github: boolean; auth0?: boolean }) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => (url === "/auth/providers" ? new Response(JSON.stringify(on), { status: 200, headers: { "Content-Type": "application/json" } }) : new Response(null, { status: 404 }))),
  )

test("both providers on: both buttons, above the email field", async () => {
  providers({ google: true, github: true })
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  const google = await screen.findByRole("button", { name: "Continue with Google" })
  const github = screen.getByRole("button", { name: "Continue with GitHub" })
  const email = screen.getByLabelText("Email")
  expect(google.compareDocumentPosition(email) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(github.compareDocumentPosition(email) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test("only GitHub on: only its button", async () => {
  providers({ google: false, github: true })
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  await screen.findByRole("button", { name: "Continue with GitHub" })
  expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull()
})

test("only Auth0 on: only its button, which starts the tenant's sign-in", async () => {
  providers({ google: false, github: false, auth0: true })
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  await screen.findByRole("button", { name: "Continue with Auth0" })
  expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull()
  expect(screen.queryByRole("button", { name: "Continue with GitHub" })).toBeNull()
})

test("a refused provider sign-in says so once, and leaves the address", async () => {
  providers({ google: true, github: false })
  window.history.replaceState(null, "", "/sign-in?error=oauth_refused")
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  expect((await screen.findByRole("alert")).textContent).toBe(PROVIDER_REFUSED)
  expect(window.location.pathname + window.location.search).toBe("/")
})
