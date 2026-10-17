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
    ["workers", "Workers"],
    ["provider-probes", "ProviderProbes"],
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

describe("issues and alert events", () => {
  const all = `from=${new Date(0).toISOString()}&to=${new Date("2100-01-01").toISOString()}`
  test.each([
    ["issues", "IssueList"],
    ["alert-events", "AlertEvents"],
  ])("%s answers in the contract shape for a range, and refuses a missing range", async (path, schema) => {
    const res = await fetch(`${base}/${path}?${all}`)
    expect(res.status).toBe(200)
    expect(valid(schema, await res.json())).toBe("")
    expect((await fetch(`${base}/${path}`)).status).toBe(400)
  })

  test("a range before every capture drops every timed hit and keeps the standing ones, which have no time", async () => {
    const body = await (await fetch(`${base}/issues?from=2000-01-01T00:00:00Z&to=2000-01-02T00:00:00Z`)).json()
    expect(body.items.length).toBeGreaterThan(0)
    expect(body.items.every((i: { hits: { at: string | null }[] }) => i.hits.every((h) => h.at === null))).toBe(true)
    expect(body.items.every((i: { source: string }) => i.source !== "calls")).toBe(true)
  })
})

describe("getCallStats", () => {
  // The span of the captures, as the console asks for it: a week ending after the latest call.
  const range = `from=${new Date("2026-10-08T00:00:00Z").toISOString()}&to=${new Date("2026-10-15T00:00:00Z").toISOString()}`
  const stats = async () => (await fetch(`${base}/call-stats?${range}`)).json()
  const page = async () => (await fetch(`${base}/calls?limit=100&${range}`)).json()

  test("answers in the contract shape for a range, and refuses a missing range", async () => {
    const res = await fetch(`${base}/call-stats?${range}`)
    expect(res.status).toBe(200)
    expect(valid("CallStats", await res.json())).toBe("")
    expect((await fetch(`${base}/call-stats`)).status).toBe(400)
  })

  test("agrees with the Calls list's totals for the same range: calls, measured turns, median and over budget", async () => {
    const [s, p] = await Promise.all([stats(), page()])
    expect(s.callCount).toBe(p.totals.callCount)
    expect(s.callCount).toBeGreaterThan(0)
    expect(s.answers.turns).toBe(p.totals.answers.turns)
    expect(s.answers.p50Ms).toBe(p.totals.answers.p50Ms)
    expect(s.answers.overBudget).toBe(p.totals.answers.overBudget)
    expect(s.byChannel).toEqual(p.totals.byChannel)
  })

  test("each agent row counts exactly the calls the list names that agent on, and every call has one row", async () => {
    const [s, p] = await Promise.all([stats(), page()])
    for (const a of s.agents) {
      const mine = p.items.filter((c: { agentKey: string | null }) => c.agentKey === a.key)
      expect(a.callCount).toBe(mine.length)
      expect(a.calls.map((c: { callId: string }) => c.callId).sort()).toEqual(mine.map((c: { id: string }) => c.id).sort())
    }
    expect(s.agents.reduce((n: number, a: { callCount: number }) => n + a.callCount, 0)).toBe(s.callCount)
    expect(new Set(s.agents.map((a: { key: string | null }) => a.key)).size).toBe(s.agents.length)
  })

  test("every turn value names its call, turn and agent as the list does", async () => {
    const [s, p] = await Promise.all([stats(), page()])
    expect(s.answers.values.length).toBe(s.answers.turns)
    for (const v of s.answers.values) {
      const call = p.items.find((c: { id: string }) => c.id === v.callId)
      expect(call.agentKey).toBe(v.agentKey)
      expect(call.room).toBe(v.room)
      expect(call.answerMs).toContain(v.ms)
    }
  })

  test("a call with no measured answer keeps a null median in its agent's strip, not zero", async () => {
    const [s, p] = await Promise.all([stats(), page()])
    const unmeasured = p.items.filter((c: { answerMsMedian: number | null }) => c.answerMsMedian === null)
    expect(unmeasured.length).toBeGreaterThan(0)
    const strips = s.agents.flatMap((a: { calls: { callId: string; answerP50Ms: number | null }[] }) => a.calls)
    for (const c of unmeasured) expect(strips.find((x: { callId: string }) => x.callId === c.id).answerP50Ms).toBeNull()
  })

  test("the issue counts are the Issues list's groups for the same range, by severity", async () => {
    const [s, list] = await Promise.all([stats(), (await fetch(`${base}/issues?${range}`)).json()])
    for (const sev of ["high", "medium", "low", "info"]) expect(s.issueCounts[sev]).toBe(list.items.filter((i: { severity: string }) => i.severity === sev).length)
  })

  test("a stage is reported by no more turns than Turn anatomy counts, and a range with no calls has no stages", async () => {
    const s = await stats()
    expect(s.stages.length).toBeGreaterThan(0)
    for (const st of s.stages) expect(st.turns).toBeLessThanOrEqual(s.anatomyTurns)
    const empty = await (await fetch(`${base}/call-stats?from=2000-01-01T00:00:00Z&to=2000-01-03T00:00:00Z`)).json()
    expect(valid("CallStats", empty)).toBe("")
    expect(empty.callCount).toBe(0)
    expect(empty.answers.p50Ms).toBeNull()
    expect(empty.stages).toEqual([])
    expect(empty.byDay.map((d: { date: string }) => d.date)).toEqual(["2000-01-01", "2000-01-02"])
  })

  test("the hourly and daily counts each add up to the call count, and turn values come newest call first", async () => {
    const [s, p] = await Promise.all([stats(), page()])
    expect(s.byHour.length).toBe(7 * 24)
    expect(s.byHour.reduce((n: number, h: { callCount: number }) => n + h.callCount, 0)).toBe(s.callCount)
    expect(s.byDay.reduce((n: number, d: { callCount: number }) => n + d.callCount, 0)).toBe(s.callCount)
    const started = new Map(p.items.map((c: { id: string; startedAt: string }) => [c.id, Date.parse(c.startedAt)]))
    const at = s.answers.values.map((v: { callId: string }) => started.get(v.callId) as number)
    expect(at).toEqual([...at].sort((a, b) => b - a))
  })

  test("a call starting exactly at to is left out; at from it is kept", async () => {
    const first = (await (await fetch(`${base}/calls?limit=1&sort=startedAt&order=asc`)).json()).items[0]
    const at = new Date(first.startedAt).toISOString()
    const before = await (await fetch(`${base}/call-stats?from=2000-01-01T00:00:00Z&to=${at}`)).json()
    const from = await (await fetch(`${base}/call-stats?from=${at}&to=2100-01-01T00:00:00Z`)).json()
    const all = await (await fetch(`${base}/call-stats?from=2000-01-01T00:00:00Z&to=2100-01-01T00:00:00Z`)).json()
    expect(before.callCount).toBe(0)
    expect(from.callCount).toBe(all.callCount)
  }, 20_000)

  test("the stage chain and providers stay within what was measured", async () => {
    const s = await stats()
    expect(s.chain.length).toBeGreaterThan(0)
    for (const scope of [s, ...s.agents]) {
      for (const st of scope.chain) {
        expect(st.turns).toBeLessThanOrEqual(scope.anatomyTurns)
        expect(st.maxMs).toBeGreaterThanOrEqual(st.medianMs)
      }
    }
    for (const a of s.agents) {
      for (const pu of a.providers) {
        expect(pu.callCount).toBeLessThanOrEqual(a.callCount)
        expect(pu.roles.length).toBeGreaterThan(0)
      }
      expect(a.answerTurns).toBe(s.answers.values.filter((v: { agentKey: string | null }) => v.agentKey === a.key).length)
    }
    // The console and realtime sessions name no agent; their realtime turns are that row's, nobody else's.
    const realtime = s.agents.filter((a: { chain: { stage: string }[] }) => a.chain.some((c) => c.stage === "realtime"))
    expect(realtime.map((a: { key: string | null }) => a.key)).toEqual([null])
  })
})
