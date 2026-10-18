// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { CopyCommand } from "../src/components/copy-command.tsx"

// CopyCommand says "Copied" only after the clipboard write succeeds: never when the clipboard is
// missing or the write is refused (from the 06c review, where a copy button claimed it either way).
// Clicks go through fireEvent: userEvent installs its own clipboard over the one each test sets.

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function clipboard(writeText: ((text: string) => Promise<void>) | undefined) {
  Object.defineProperty(navigator, "clipboard", { value: writeText ? { writeText } : undefined, configurable: true })
}

test("a successful write says Copied", async () => {
  const writeText = vi.fn(async () => {})
  clipboard(writeText)
  render(<CopyCommand text="vgk_example" label="Key" />)
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy command" })))
  expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy()
  expect(writeText).toHaveBeenCalledWith("vgk_example")
})

test("a refused write does not say Copied", async () => {
  clipboard(vi.fn(async () => Promise.reject(new Error("denied"))))
  render(<CopyCommand text="vgk_example" label="Key" />)
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy command" })))
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull()
})

test("no clipboard API does not say Copied", async () => {
  clipboard(undefined)
  render(<CopyCommand text="vgk_example" label="Key" />)
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy command" })))
  await new Promise((r) => setTimeout(r, 20))
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull()
})
