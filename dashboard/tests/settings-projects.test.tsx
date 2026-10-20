// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { setWorkspaces } from "../src/lib/workspaces.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"

vi.hoisted(() => {
 window.matchMedia ??= (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }) as MediaQueryList
})
const { Organisation } = await import("../src/features/settings/settings-dialog.tsx")
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setWorkspaces(null) })

test.each([false, true])("organisation rows preserve their scope when second-project access is denied: %s", async (denied) => {
 scopeRewrite.input({ url: new URL("http://localhost/org/default/project/first/overview") })
 setWorkspaces([{ id: "default", name: "Organisation", projects: [{ id: "first", name: "First" }, { id: "second", name: "Second" }] }])
 const asked: string[] = []
 vi.stubGlobal("fetch", async (request: Request) => {
  asked.push(request.url)
  if (request.url.endsWith("/entitlement")) return new Response(JSON.stringify({ code: "not_found", message: "Not enabled", requestId: "test" }), { status: 404, headers: { "Content-Type": "application/json" } })
  if (denied && request.url.includes("/second/")) return new Response(JSON.stringify({ code: "forbidden", message: "Denied", requestId: "test" }), { status: 403, headers: { "Content-Type": "application/json" } })
  const body = request.url.includes("/media-server") ? { server: { url: request.url.includes("/first/") ? "wss://first.example" : "wss://second.example" } } : {}
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
 })
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
 render(<QueryClientProvider client={client}><Organisation /></QueryClientProvider>)
 const first = (await screen.findByText("First")).closest('[role="listitem"]') as HTMLElement
 const second = (await screen.findByText("Second")).closest('[role="listitem"]') as HTMLElement
 expect(await within(first).findByText("wss://first.example")).toBeTruthy()
 expect(await within(second).findByText(denied ? "Could not be read" : "wss://second.example")).toBeTruthy()
 expect(within(second).queryByText("wss://first.example")).toBeNull()
 expect(within(first).getByText("Current project")).toBeTruthy()
 expect(within(second).queryByText("Current project")).toBeNull()
 expect(asked.filter((url) => url.endsWith("/media-server"))).toHaveLength(2)
 client.clear()
})
