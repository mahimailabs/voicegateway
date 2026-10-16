import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { Ajv2020 } from "ajv/dist/2020.js"
import { setupServer } from "msw/node"
import { afterAll, beforeAll, describe, expect, test } from "vitest"
import { parse } from "yaml"
import { handlers } from "../src/mocks/handlers.ts"
import { capturedCalls } from "../src/mocks/fixtures.ts"

// The mock API answers through the contract: every response is checked against docs/api/openapi.yaml
// itself, not against types written for the test.
const here = path.dirname(fileURLToPath(import.meta.url))
const doc = parse(fs.readFileSync(path.resolve(here, "../../docs/api/openapi.yaml"), "utf8")) as { components: { schemas: Record<string, unknown> } }
const ajv = new Ajv2020({ strict: false, validateFormats: false, allErrors: true })
ajv.addSchema({ $id: "contract", components: doc.components })
const valid = (schema: string, body: unknown) => {
  const check = ajv.getSchema(`contract#/components/schemas/${schema}`)!
  return check(body) ? "" : ajv.errorsText(check.errors)
}

const base = "http://console.test/api/v1/org/default/project/default-project"
const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

describe("listCalls", () => {
  test("pages through every captured call once, each page in the contract shape", async () => {
    const seen: string[] = []
    let cursor: string | null = null
    let snapshot = ""
    do {
      const q: URLSearchParams = new URLSearchParams({ limit: "3" })
      if (cursor) q.set("cursor", cursor), q.set("snapshot", snapshot)
      const res = await fetch(`${base}/calls?${q}`)
      expect(res.status).toBe(200)
      const page = await res.json()
      expect(valid("CallPage", page)).toBe("")
      expect(page.items.length).toBeLessThanOrEqual(3)
      seen.push(...page.items.map((c: { id: string }) => c.id))
      cursor = page.page.nextCursor
      snapshot = page.page.snapshot
    } while (cursor)
    expect(seen.sort()).toEqual(capturedCalls().map((c) => c.id).sort())
  })

  test("a call with no measured answer reports null, never an empty or zero answer time", async () => {
    const page = await (await fetch(`${base}/calls?limit=100`)).json()
    const unmeasured = page.items.filter((c: { answerMsMedian: number | null }) => c.answerMsMedian === null)
    expect(unmeasured.length).toBeGreaterThan(0)
    for (const c of unmeasured) expect(c.answerMs).toBeNull()
  })

  test.each([
    ["limit out of range", "?limit=0", 400],
    ["cursor without its snapshot", "?cursor=3", 400],
  ])("%s is refused in the error shape", async (_name, query, status) => {
    const res = await fetch(`${base}/calls${query}`)
    expect(res.status).toBe(status)
    expect(valid("Error", await res.json())).toBe("")
  })
})

describe("getCall", () => {
  test("a captured call is returned whole, in the contract shape", async () => {
    const id = capturedCalls()[0].id
    const res = await fetch(`${base}/calls/${encodeURIComponent(id)}`)
    expect(res.status).toBe(200)
    const call = await res.json()
    expect(valid("Call", call)).toBe("")
    expect(call.id).toBe(id)
  })

  test("an unknown id is 404, not the first fixture", async () => {
    const res = await fetch(`${base}/calls/no-such-call`)
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(valid("Error", body)).toBe("")
    expect(body.code).toBe("not_found")
  })

  test("another project's path is 404", async () => {
    const res = await fetch(`http://console.test/api/v1/org/default/project/other/calls/${encodeURIComponent(capturedCalls()[0].id)}`)
    expect(res.status).toBe(404)
  })
})
