import { afterEach, expect, test, vi } from "vitest"
import { ApiError, fetchCall, shouldRetry } from "../src/lib/api.ts"

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
