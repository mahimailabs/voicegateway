// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { LiveUpdatesProvider } from "../src/components/live-updates.tsx"
import { watchLiveUpdates } from "../src/lib/live-updates.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"

vi.mock("../src/lib/live-updates.ts", () => ({ watchLiveUpdates: vi.fn(async () => {}) }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })

test("project switches, hidden tabs and unmount abort the old feed", async () => {
  const client = new QueryClient()
  let hidden = false
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden)
  const scope = (project: string) => scopeRewrite.input({ url: new URL(`http://localhost/org/test/project/${project}/live`) })
  scope("one")
  const tree = () => <QueryClientProvider client={client}><LiveUpdatesProvider><div>View</div></LiveUpdatesProvider></QueryClientProvider>
  const view = render(tree())
  await act(async () => {})
  const calls = vi.mocked(watchLiveUpdates).mock.calls
  expect(calls[0][0]).toEqual({ org: "test", project: "one" })
  scope("two"); view.rerender(tree())
  expect(calls[0][3].aborted).toBe(true)
  expect(calls[1][0]).toEqual({ org: "test", project: "two" })
  await act(async () => { hidden = true; document.dispatchEvent(new Event("visibilitychange")) })
  expect(calls[1][3].aborted).toBe(true)
  expect(calls).toHaveLength(2)
  await act(async () => { hidden = false; document.dispatchEvent(new Event("visibilitychange")) })
  expect(calls).toHaveLength(3)
  view.unmount()
  expect(calls[2][3].aborted).toBe(true)
  client.clear()
})
