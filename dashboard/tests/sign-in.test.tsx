// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, test, vi } from "vitest"
import { FIRST_ADMIN_COMMAND, SignInScreen } from "../src/features/auth/sign-in-screen.tsx"

// Acceptance check 9 of phase 06a: what the sign-in screen does with each answer from web.
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const answer = (status: number, body = "", headers: Record<string, string> = {}) =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(status === 204 ? null : body, { status, headers: { "Content-Type": "application/json", ...headers } })))

async function submit() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("Email"), "ada@example.com")
  await user.type(screen.getByLabelText("Password"), "correct horse battery staple")
  await user.click(screen.getByRole("button", { name: "Sign in" }))
}

test("204 opens the console", async () => {
  answer(204)
  const onSignedIn = vi.fn()
  render(<SignInScreen needsAdmin={false} onSignedIn={onSignedIn} />)
  await submit()
  expect(onSignedIn).toHaveBeenCalledOnce()
  expect(screen.queryByRole("alert")).toBeNull()
})

test("401 says the email or password is wrong", async () => {
  answer(401, '{"code":"unauthenticated","message":"email or password is wrong"}')
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  await submit()
  expect((await screen.findByRole("alert")).textContent).toBe("Email or password is wrong.")
})

test("429 says when to try again", async () => {
  answer(429, '{"code":"too_many_attempts"}', { "Retry-After": "120" })
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  await submit()
  expect((await screen.findByRole("alert")).textContent).toContain("Try again in 2 min")
})

test("https_required says HTTPS is required", async () => {
  answer(400, '{"code":"https_required"}')
  render(<SignInScreen needsAdmin={false} onSignedIn={vi.fn()} />)
  await submit()
  expect((await screen.findByRole("alert")).textContent).toContain("HTTPS is required")
})

test("no account yet shows the first-admin command", () => {
  render(<SignInScreen needsAdmin onSignedIn={vi.fn()} />)
  expect(screen.getByLabelText("First admin command").textContent).toBe(FIRST_ADMIN_COMMAND)
})
