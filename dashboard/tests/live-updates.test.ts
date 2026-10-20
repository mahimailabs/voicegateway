import { afterEach, expect, test, vi } from "vitest"
import { QueryClient, QueryObserver } from "@tanstack/react-query"
import { watchLiveUpdates } from "../src/lib/live-updates.ts"

const scope = { org: "test", project: "one" }
afterEach(() => vi.unstubAllGlobals())

function source() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({ start(c) { controller = c } })
  return {
    response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    event: (event: string) => controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: {}\n\n`)),
    end: () => controller.close(),
  }
}

test("refresh updates the subscribed project and renewal repairs missed changes", async () => {
  const client = new QueryClient()
  const key = ["workers", scope]
  const other = ["workers", { ...scope, project: "two" }]
  client.setQueryData(other, { generation: 99 })
  let generation = 1
  const observer = new QueryObserver(client, { queryKey: key, queryFn: async () => ({ generation }), initialData: { generation: 0 }, staleTime: Infinity })
  const unsubscribe = observer.subscribe(() => {})
  const first = source(), next = source()
  const fetcher = vi.fn().mockResolvedValueOnce(first.response).mockResolvedValueOnce(next.response)
  vi.stubGlobal("fetch", fetcher)
  const controller = new AbortController()
  const state = vi.fn()
  const running = watchLiveUpdates(scope, client, state, controller.signal)
  try {
    first.event("refresh")
    await vi.waitFor(() => expect(client.getQueryData(key)).toEqual({ generation: 1 }))
    expect(client.getQueryData(other)).toEqual({ generation: 99 })
    generation = 2
    first.event("renew"); first.end()
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
    next.event("refresh")
    await vi.waitFor(() => expect(client.getQueryData(key)).toEqual({ generation: 2 }))
    expect(state).toHaveBeenCalledWith("connected")
  } finally { controller.abort(); await running; unsubscribe(); client.clear() }
})

test.each([401, 403, 404])("denied stream %s clears only its own cached project", async (status) => {
  const client = new QueryClient()
  const key = ["workers", scope], other = ["workers", { ...scope, project: "two" }]
  client.setQueryData(key, { secret: "old telemetry" }); client.setQueryData(other, { other: true })
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("denied", { status })))
  const controller = new AbortController(), state = vi.fn()
  const running = watchLiveUpdates(scope, client, state, controller.signal)
  try {
    await vi.waitFor(() => expect(state).toHaveBeenCalledWith("disconnected"))
    expect(client.getQueryData(key)).toBeUndefined()
    expect(client.getQueryData(other)).toEqual({ other: true })
  } finally {controller.abort(); await running; client.clear()}
})

test("a dropped connection is disconnected, retains last observation, and abort cancels retry", async () => {
  const client = new QueryClient(), stream = source()
  const key = ["workers", scope]; client.setQueryData(key, { observed: true })
  const fetcher = vi.fn().mockResolvedValue(stream.response); vi.stubGlobal("fetch", fetcher)
  const controller = new AbortController(), state = vi.fn()
  const running = watchLiveUpdates(scope, client, state, controller.signal)
  stream.event("refresh"); stream.end()
  await vi.waitFor(() => expect(state).toHaveBeenCalledWith("disconnected"))
  expect(client.getQueryData(key)).toEqual({ observed: true })
  controller.abort(); await running
  expect(fetcher).toHaveBeenCalledTimes(1)
  client.clear()
})

test("a late snapshot cannot repopulate telemetry after stream access is denied", async () => {
  const client = new QueryClient(), key = ["workers", scope]
  client.setQueryData(key, { observed: "old" })
  let resolve!: (value: unknown) => void
  const pending = client.fetchQuery({ queryKey: key, queryFn: () => new Promise((done) => { resolve = done }), staleTime: 0 }).catch(() => undefined)
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("denied", { status: 403 })))
  const controller = new AbortController(), state = vi.fn()
  const running = watchLiveUpdates(scope, client, state, controller.signal)
  try {
    await vi.waitFor(() => expect(state).toHaveBeenCalledWith("disconnected"))
    resolve({ observed: "late" }); await pending
    expect(client.getQueryData(key)).toBeUndefined()
  } finally { controller.abort(); await running; client.clear() }
})


test("an unavailable stream reconnects and refreshes the durable snapshot", async () => {
  const client = new QueryClient(), stream = source()
  const key = ["workers", scope]
  const observer = new QueryObserver(client, {
    queryKey: key, queryFn: async () => ({ generation: 2 }),
    initialData: { generation: 1 }, staleTime: Infinity,
  })
  const unsubscribe = observer.subscribe(() => {})
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
    .mockResolvedValueOnce(stream.response)
  vi.stubGlobal("fetch", fetcher)
  const controller = new AbortController(), state = vi.fn()
  const running = watchLiveUpdates(scope, client, state, controller.signal)
  try {
    await vi.waitFor(() => expect(state).toHaveBeenCalledWith("disconnected"))
    expect(client.getQueryData(key)).toEqual({ generation: 1 })
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2), { timeout: 4500 })
    stream.event("refresh")
    await vi.waitFor(() => expect(client.getQueryData(key)).toEqual({ generation: 2 }))
    expect(state).toHaveBeenLastCalledWith("connected")
  } finally { controller.abort(); await running; unsubscribe(); client.clear() }
}, 6000)
