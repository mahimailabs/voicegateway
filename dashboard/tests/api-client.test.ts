import { afterEach, expect, test, vi } from "vitest"
import { ApiError, callInRoomQuery, fetchCall, shouldRetry } from "../src/lib/api.ts"

// What the console shows depends on telling "the API said no" from "no API answered".
afterEach(() => vi.unstubAllGlobals())

const answer = (body: string, status: number, type: string) => vi.stubGlobal("fetch", async () => new Response(body, { status, headers: { "Content-Type": type } }))

test.each([
  ["the dev server's own page with 200", "<!doctype html>", 200, "text/html"],
  ["a 404 page that is not the API's", "Not Found", 404, "text/plain"],
  ["JSON without the contract's error code", '{"error":"nope"}', 502, "application/json"],
  ["a body that is not JSON", "{", 200, "application/json"],
])("%s is no API, status 0", async (_name, body, status, type) => {
  answer(body, status, type)
  const err = await fetchCall("x").catch((e: unknown) => e)
  expect(err).toBeInstanceOf(ApiError)
  expect((err as ApiError).status).toBe(0)
})

test("the contract's error body keeps its status and code", async () => {
  answer('{"code":"not_found","message":"No such call in this project.","requestId":"r1"}', 404, "application/json")
  const err = (await fetchCall("x").catch((e: unknown) => e)) as ApiError
  expect([err.status, err.code, err.message]).toEqual([404, "not_found", "No such call in this project."])
})

test("a refused network request is no API", async () => {
  vi.stubGlobal("fetch", async () => {
    throw new TypeError("Failed to fetch")
  })
  expect(((await fetchCall("x").catch((e: unknown) => e)) as ApiError).status).toBe(0)
})

test("4xx and no API are not retried; a 5xx is retried once", () => {
  expect(shouldRetry(0, new ApiError(404, "not_found", ""))).toBe(false)
  expect(shouldRetry(0, new ApiError(0, "no_api", ""))).toBe(false)
  expect(shouldRetry(0, new ApiError(503, "source_unavailable", ""))).toBe(true)
  expect(shouldRetry(1, new ApiError(503, "source_unavailable", ""))).toBe(false)
})

test("a room opens the call with its own SID, never another call in a reused room name", async () => {
  const row = (id: string, roomSid: string | null) => ({ id, room: "support", roomSid })
  const page = { items: [row("newer", "RM_other"), row("unknown-sid", null), row("mine", "RM_mine")], page: { nextCursor: null, snapshot: "s" }, totals: {} }
  const asked: string[] = []
  vi.stubGlobal("fetch", async (input: Request) => {
    asked.push(input.url)
    return new Response(JSON.stringify(page), { status: 200, headers: { "Content-Type": "application/json" } })
  })
  expect(await callInRoomQuery("support", "RM_mine").queryFn()).toBe("mine")
  expect(await callInRoomQuery("support", "RM_absent").queryFn()).toBeNull()
  expect(new URL(asked[0], "http://x").searchParams.get("room")).toBe("support")
})

test("a room's call on a later page is found, and a search cut short is an error, not no call", async () => {
  const row = (id: string, roomSid: string) => ({ id, room: "support", roomSid })
  const pages: Record<string, unknown> = {
    first: { items: [row("a", "RM_a")], page: { nextCursor: "2", snapshot: "s" }, totals: {} },
    second: { items: [row("b", "RM_b")], page: { nextCursor: null, snapshot: "s" }, totals: {} },
  }
  vi.stubGlobal("fetch", async (input: Request) => {
    const body = new URL(input.url).searchParams.get("cursor") === "2" ? pages.second : pages.first
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })
  })
  expect(await callInRoomQuery("support", "RM_b").queryFn()).toBe("b")
  expect(await callInRoomQuery("support", "RM_absent").queryFn()).toBeNull()
  // Every page has another after it: the search stops and says so.
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(pages.first), { status: 200, headers: { "Content-Type": "application/json" } }))
  await expect(callInRoomQuery("support", "RM_absent").queryFn()).rejects.toBeInstanceOf(ApiError)
})

test("settings requests retain their selected scope through navigation and carry cancellation", async () => {
  const { projectSettingsQuery } = await import("../src/lib/api.ts")
  const { scopeRewrite } = await import("../src/lib/url-state.ts")
  scopeRewrite.input({ url: new URL("http://localhost/org/one/project/first/overview") })
  const query = projectSettingsQuery()
  scopeRewrite.input({ url: new URL("http://localhost/org/two/project/second/overview") })
  const controller = new AbortController()
  let sent: Request | undefined
  vi.stubGlobal("fetch", async (request: Request) => { sent = request; return new Response("{}", { headers: { "Content-Type": "application/json" } }) })
  await query.queryFn({ signal: controller.signal })
  expect(sent?.url).toContain("/org/one/project/first/settings")
  expect(query.queryKey).toEqual(["settings", { org: "one", project: "first" }])
  controller.abort()
  expect(sent?.signal.aborted).toBe(true)
  scopeRewrite.input({ url: new URL("http://localhost/org/default/project/default-project/overview") })
})

test("permission loss clears retained project settings", async () => {
  const { projectSettingsQuery } = await import("../src/lib/api.ts")
  const { createQueryClient } = await import("../src/lib/query-client.ts")
  const client = createQueryClient()
  const query = projectSettingsQuery()
  client.setQueryData(query.queryKey, { thresholds: { answerMs: 2500 }, revision: 4 })
  answer('{"code":"forbidden","message":"Access removed","requestId":"r"}', 403, "application/json")
  await expect(client.fetchQuery({ ...query, staleTime: 0 })).rejects.toBeInstanceOf(ApiError)
  expect(client.getQueryData(query.queryKey)).toBeUndefined()
  client.clear()
})
