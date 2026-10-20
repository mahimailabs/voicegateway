// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react"
import { QueryClient, QueryClientProvider, useQuery, onlineManager, focusManager } from "@tanstack/react-query"
import { afterEach, expect, test, vi } from "vitest"
import { mediaServerQuery, workersQuery, cloudAgentsQuery, resourcesQuery, accessDenied, ApiError } from "../src/lib/api.ts"
import { createQueryClient } from "../src/lib/query-client.ts"
import { scopeRewrite } from "../src/lib/url-state.ts"
import { SourceStatus } from "../src/components/source-status.tsx"
import { render, screen } from "@testing-library/react"

const open = (project: string) => scopeRewrite.input({ url: new URL(`http://localhost/org/test/project/${project}/live`) })
const answer = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })
afterEach(() => { cleanup(); onlineManager.setOnline(true); focusManager.setFocused(undefined); vi.useRealTimers(); vi.unstubAllGlobals() })

test.each([mediaServerQuery, workersQuery, cloudAgentsQuery, resourcesQuery])("queries bind request scope when constructed", async (query) => {
  open("first")
  const options = query()
  open("second")
  const asked: string[] = []
  vi.stubGlobal("fetch", async (request: Request) => { asked.push(request.url); return answer({}) })
  await options.queryFn({ signal: new AbortController().signal })
  expect(asked[0]).toContain("/org/test/project/first/")
  expect(options.queryKey[1]).toEqual({ org: "test", project: "first" })
})

test("refresh keeps last data on disconnect, then replaces it on reconnect", async () => {
  vi.useFakeTimers()
  focusManager.setFocused(true)
  let fail = false
  let generation = 1
  vi.stubGlobal("fetch", async () => { if (fail) throw new TypeError("offline"); return answer({ generation }) })
  const client = createQueryClient()
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => { const read = useQuery(mediaServerQuery()); return { data: read.data, isError: read.isError } }, { wrapper })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.data).toEqual({ generation: 1 })
  fail = true
  await act(async () => { await vi.advanceTimersByTimeAsync(10050) })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.isError).toBe(true)
  expect(result.current.data).toEqual({ generation: 1 })
  fail = false; generation = 2
  await act(async () => { onlineManager.setOnline(false); onlineManager.setOnline(true); await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.data).toEqual({ generation: 2 })
  expect(result.current.isError).toBe(false)
  unmount(); client.clear()
})

test("a mounted view ages even when no new HTTP answer arrives", async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"))
  const source = { lastReadAt: new Date().toISOString(), lastGoodReadAt: new Date().toISOString(), errorClass: null }
  const query = { isError: false, fetchStatus: "idle", refetch: vi.fn() }
  const view = render(<SourceStatus name="SFU" source={source} interval={10000} query={query} />)
  expect(screen.getByText("Updating")).toBeTruthy()
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(screen.getByText("Stale samples")).toBeTruthy()
  view.rerender(<SourceStatus name="SFU" source={source} interval={10000} query={{ ...query, isError: true }} />)
  expect(screen.getByText("API disconnected")).toBeTruthy()
})

test("permission denial cannot display retained telemetry", () => {
  for (const code of [401, 403, 404]) expect(accessDenied(new ApiError(code, "denied", ""))).toBe(true)
  expect(accessDenied(new ApiError(503, "unavailable", ""))).toBe(false)
})

test("leaving a live view aborts its in-flight request", async () => {
  let requestSignal: AbortSignal | undefined
  vi.stubGlobal("fetch", (request: Request) => {
    requestSignal = request.signal
    return new Promise<Response>((_resolve, reject) => request.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))))
  })
  const client = new QueryClient()
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { unmount } = renderHook(() => useQuery(workersQuery()), { wrapper })
  await act(async () => { await Promise.resolve() })
  expect(requestSignal?.aborted).toBe(false)
  unmount()
  expect(requestSignal?.aborted).toBe(true)
  client.clear()
})


test.each([401, 403, 404])("denial %s clears telemetry for every observer and recovers when access returns", async (status) => {
  vi.useFakeTimers()
  const client = createQueryClient()
  const other = ["media-server", { org: "test", project: "other" }]
  client.setQueryData(other, { rooms: ["other-project"] })
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  for (const source of [mediaServerQuery, workersQuery, cloudAgentsQuery, resourcesQuery]) {
    let denied = false
    vi.stubGlobal("fetch", async () => denied
      ? new Response(JSON.stringify({ code: "denied", message: "Access denied" }), { status, headers: { "Content-Type": "application/json" } })
      : answer({ generation: 1 }))
    const sourceOptions = source()
    const options = { ...sourceOptions, queryFn: (context: { signal: AbortSignal }): Promise<unknown> => sourceOptions.queryFn(context) }
    const { result, unmount } = renderHook(() => {
      const primary = useQuery(options)
      const secondary = useQuery(options).data
      return { primary, secondary }
    }, { wrapper })
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    expect(result.current.secondary).toEqual({ generation: 1 })
    denied = true
    await act(async () => { await result.current.primary.refetch(); await vi.advanceTimersByTimeAsync(10) })
    expect(result.current.primary.isError).toBe(true)
    expect(result.current.secondary).toBeUndefined()
    expect(client.getQueryData(options.queryKey)).toBeUndefined()
    expect(client.getQueryData(other)).toEqual({ rooms: ["other-project"] })
    denied = false
    await act(async () => { await result.current.primary.refetch(); await vi.advanceTimersByTimeAsync(10) })
    expect(result.current.secondary).toEqual({ generation: 1 })
    unmount()
  }
  client.clear()
})
