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
      if (cursor) {
      q.set("cursor", cursor)
      q.set("snapshot", snapshot)
    }
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

  test("a call with nothing to check reports unknown issues, never zero", async () => {
    const page = await (await fetch(`${base}/calls?limit=100`)).json()
    const bare = capturedCalls().filter((c) => !c.spans?.length && !c.turns?.length && !c.events?.length)
    expect(bare.length).toBeGreaterThan(0)
    for (const c of bare) {
      const row = page.items.find((s: { id: string }) => s.id === c.id)
      expect(row.issues.high).toBeNull()
      expect(row.issues.medium).toBeNull()
    }
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

// Every page of a filtered list.
async function all(query: string) {
  const items: { id: string; [k: string]: unknown }[] = []
  let first: { totals: Record<string, unknown> } | null = null
  let cursor: string | null = null
  let snapshot = ""
  do {
    const q = new URLSearchParams(query)
    q.set("limit", "2")
    if (cursor) {
      q.set("cursor", cursor)
      q.set("snapshot", snapshot)
    }
    const res = await fetch(`${base}/calls?${q}`)
    expect(res.status).toBe(200)
    const page = await res.json()
    expect(valid("CallPage", page)).toBe("")
    first ??= page
    items.push(...page.items)
    cursor = page.page.nextCursor
    snapshot = page.page.snapshot
  } while (cursor)
  return { items, totals: first!.totals as { callCount: number; issues: Record<string, number>; byChannel: Record<string, number>; answers: { turns: number; valuesMs: number[] } } }
}

describe("listCalls filters", () => {
  test("each filter returns only matching calls, and the totals cover every match, not a page", async () => {
    const everything = await all("")
    const cases: [string, (c: Record<string, unknown>) => boolean][] = [
      ["channel=local&channel=console", (c) => ["local", "console"].includes(c.channel as string)],
      ["issues=high", (c) => ((c.issues as { high: number | null }).high ?? 0) > 0],
      ["answerMsMin=2000", (c) => c.answerMsMedian !== null && (c.answerMsMedian as number) >= 2000],
      ["durationSecondsMax=90", (c) => c.durationSeconds !== null && (c.durationSeconds as number) <= 90],
      ["unknown=answerMs", (c) => c.answerMsMedian === null],
      ["recorded=true", (c) => c.recorded === true],
    ]
    for (const [query, want] of cases) {
      const { items, totals } = await all(query)
      const expected = everything.items.filter(want).map((c) => c.id).sort()
      expect(items.map((c) => c.id).sort(), query).toEqual(expected)
      expect(totals.callCount, query).toBe(expected.length)
      const answers = items.flatMap((c) => (c.answerMs as number[] | null) ?? [])
      expect(totals.answers.turns, query).toBe(answers.length)
    }
  })

  test("an unknown value never matches a bound", async () => {
    const { items } = await all("turnsMin=0")
    expect(items.every((c) => c.turnCount !== null)).toBe(true)
  })

  test("calls with nothing to check are counted as unknown issues, not as clean", async () => {
    const { totals } = await all("")
    expect(totals.issues.unknown).toBeGreaterThan(0)
    expect(totals.issues.high + totals.issues.medium + totals.issues.none + totals.issues.unknown).toBe(totals.callCount)
  })

  test("the range is inclusive at from and exclusive at to", async () => {
    const { items } = await all("sort=startedAt&order=asc")
    const first = items[0].startedAt as string
    expect((await all(`from=${first}&to=${first}`)).items).toEqual([])
    expect((await all(`from=${first}&to=${new Date(Date.parse(first) + 1).toISOString()}`)).items.map((c) => c.id)).toContain(items[0].id)
  })

  test("sorting puts unknown values last in both orders", async () => {
    for (const order of ["asc", "desc"]) {
      const { items } = await all(`sort=answerMs&order=${order}`)
      const values = items.map((c) => c.answerMsMedian as number | null)
      const firstUnknown = values.indexOf(null)
      expect(values.slice(firstUnknown).every((v) => v === null), order).toBe(true)
      const known = values.slice(0, firstUnknown) as number[]
      expect(known, order).toEqual([...known].sort((a, b) => (order === "asc" ? a - b : b - a)))
    }
  })

  test("a cursor used with other filters is refused", async () => {
    const page = await (await fetch(`${base}/calls?limit=1&channel=local`)).json()
    const res = await fetch(`${base}/calls?limit=1&channel=web&cursor=${page.page.nextCursor}&snapshot=${page.page.snapshot}`)
    expect(res.status).toBe(400)
  })

  test("a value the contract does not allow is refused", async () => {
    expect((await fetch(`${base}/calls?channel=fax`)).status).toBe(400)
    expect((await fetch(`${base}/calls?turnsMin=1.5`)).status).toBe(400)
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

  test("another organisation's path is 404", async () => {
    const res = await fetch(`http://console.test/api/v1/org/other/project/default-project/calls`)
    expect(res.status).toBe(404)
  })

  test("another project's path is 404", async () => {
    const res = await fetch(`http://console.test/api/v1/org/default/project/other/calls/${encodeURIComponent(capturedCalls()[0].id)}`)
    expect(res.status).toBe(404)
  })
})

describe("LiveKit reads", () => {
  test.each([
    ["egress", "Egress"],
    ["ingress", "Ingress"],
    ["sip", "Sip"],
    ["media-server", "MediaServer"],
    ["agents", "CloudAgents"],
    ["capacity", "CapacityRun"],
    ["snapshot", "ProjectSnapshot"],
  ])("%s answers in the contract shape, and 404 for another project", async (path, schema) => {
    const res = await fetch(`${base}/${path}`)
    expect(res.status).toBe(200)
    expect(valid(schema, await res.json())).toBe("")
    expect((await fetch(`http://console.test/api/v1/org/default/project/other/${path}`)).status).toBe(404)
  })

  test("captured Unix times become RFC 3339, and an unreported time stays null", async () => {
    const eg = await (await fetch(`${base}/egress`)).json()
    const times = eg.egress.flatMap((e: { startedAt: string | null; history: { at: string | null }[] }) => [e.startedAt, ...e.history.map((h) => h.at)])
    expect(times.length).toBeGreaterThan(0)
    for (const t of times) expect(t === null || !Number.isNaN(Date.parse(t))).toBe(true)
  })
})
