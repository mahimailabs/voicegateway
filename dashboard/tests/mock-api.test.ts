import fs from "node:fs"
import http from "node:http"
import type { AddressInfo } from "node:net"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { Ajv2020 } from "ajv/dist/2020.js"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, test, vi } from "vitest"
import { parse } from "yaml"
import { handlers } from "../src/mocks/handlers.ts"
import { capturedCalls } from "../src/mocks/fixtures.ts"
import { buildModel, turnCount } from "../src/features/call/call-model.ts"
import { QueryClient } from "@tanstack/react-query"
import type { AlertSetup } from "../src/lib/api-types"
import { localSave, writeLocal } from "../src/lib/local-store.ts"
import { type Setup, setupFrom } from "../src/features/alerts/alerts-model.ts"
import { agentsFrom, checks as agentChecks } from "../src/features/agents/agents-model.ts"
import { fromContract } from "../src/features/media-server/room-drawer.tsx"
import { checks as sipChecks, routes } from "../src/features/sip/sip-model.ts"

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

  test("the list's turn count is the number of turns the call view shows, for every captured call", async () => {
    const list = (await (await fetch(`${base}/calls?limit=100`)).json()).items as { id: string; turnCount: number | null }[]
    expect(list.length).toBe(capturedCalls().length)
    for (const row of list) {
      const call = await (await fetch(`${base}/calls/${encodeURIComponent(row.id)}`)).json()
      // The drawer's count: a known count is the same number, and unknown is unknown on both sides.
      expect(turnCount(call, buildModel(call)), row.id).toBe(row.turnCount)
    }
    // The phone test call has four agent_turn spans, but only two of them spoke.
    expect(list.find((r) => r.id.includes("937b5111"))?.turnCount).toBe(2)
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

  test("the agent and phone issues are the checks the Agents and Phone bridge pages run over the reads they make", async () => {
    const body = await (await fetch(`${base}/issues?${all}`)).json()
    const titles = (src: string) => [...new Set(body.items.filter((i: { source: string }) => i.source === src).map((i: { title: string }) => i.title))].sort()
    const ms = fromContract(await (await fetch(`${base}/media-server`)).json())
    const sip = await (await fetch(`${base}/sip`)).json()
    const hints = ms.rooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) })))
    expect(titles("agent_workers")).toEqual([...new Set(agentChecks(agentsFrom(await (await fetch(`${base}/agents`)).json(), ms)).map((c) => c.title))].sort())
    expect(titles("phone_bridge")).toEqual([...new Set(sipChecks(sip, routes(sip, hints)).map((c) => c.title))].sort())
    expect(titles("agent_workers").length + titles("phone_bridge").length).toBeGreaterThan(0)
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
    // A century-long range still answers with at most a week of hours.
    expect(all.byHour.length).toBe(168)
  })

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

// Settings come back from what the console saved in this browser; each case brings its own storage.
function storage(saved: Record<string, unknown>, blocked: boolean | "writes" = false) {
  const items = new Map(Object.entries(saved).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]))
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => {
      if (blocked === true) throw new Error("blocked")
      return items.get(k) ?? null
    },
    setItem: (k: string, v: string) => {
      if (blocked) throw new Error("blocked")
      items.set(k, v)
    },
  })
}
const get = async (url: string) => {
  const res = await fetch(url)
  return { status: res.status, body: await res.json() }
}

describe("settings reads", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test("an unconfigured project reports no bucket, retention or origins as null, never zero, and the contract's default thresholds", async () => {
    storage({})
    const { status, body } = await get(`${base}/settings`)
    expect(status).toBe(200)
    expect(valid("ProjectSettings", body)).toBe("")
    expect(body).toEqual({ thresholds: { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }, storageBucket: null, transcriptRetentionDays: null, traceRetentionDays: null, recordingRetentionDays: null, allowedOrigins: null })
  })

  test.each([
    ["valid values are served as saved", { answerMs: 1200, joinMs: 2500, ttsStartMs: 500 }, [1200, 2500, 500]],
    ["each invalid field falls back on its own", { answerMs: "1500", joinMs: 3000, ttsStartMs: -1 }, [1500, 3000, 1000]],
    ["zero and NaN are not thresholds", { answerMs: 0, joinMs: null, ttsStartMs: {} }, [1500, 2000, 1000]],
    ["malformed storage serves the defaults", "{broken", [1500, 2000, 1000]],
    ["a non-object serves the defaults", "[]", [1500, 2000, 1000]],
  ])("thresholds: %s", async (_name, saved, expected) => {
    storage({ "vg.thresholds": saved })
    const { body } = await get(`${base}/settings`)
    expect(valid("ProjectSettings", body)).toBe("")
    expect([body.thresholds.answerMs, body.thresholds.joinMs, body.thresholds.ttsStartMs]).toEqual(expected)
  })

  test("blocked storage serves the defaults", async () => {
    storage({}, true)
    expect((await get(`${base}/settings`)).body.thresholds).toEqual({ answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 })
  })

  test("a saved answer threshold is the budget the server judges calls by", async () => {
    storage({ "vg.thresholds": { answerMs: 900 } })
    const range = "from=2000-01-01T00:00:00Z&to=2100-01-01T00:00:00Z"
    const stats = (await get(`${base}/call-stats?${range}`)).body
    const page = (await get(`${base}/calls?limit=1`)).body
    expect(stats.answers.budgetMs).toBe(900)
    expect(stats.answers.overBudget).toBe(stats.answers.values.filter((v: { ms: number }) => v.ms > 900).length)
    expect(page.totals.answers.budgetMs).toBe(900)
  })

  test("issue decisions come back as saved, in RFC 3339, and a malformed one is left out", async () => {
    const at = Date.UTC(2026, 9, 17, 10, 0, 0)
    storage({ "vg.issue-state": { slow: { state: "resolved", at }, sip: { state: "ignored", at }, bad: { state: "maybe", at }, undated: { state: "resolved" } } })
    const { status, body } = await get(`${base}/issue-dispositions`)
    expect(status).toBe(200)
    expect(valid("IssueDispositions", body)).toBe("")
    expect(body.items).toEqual([
      { issueId: "slow", state: "resolved", at: "2026-10-17T10:00:00.000Z" },
      { issueId: "sip", state: "ignored", at: "2026-10-17T10:00:00.000Z" },
    ])
  })

  test("alert setup names every rule; an unchanged rule is on and sends nowhere, and an unknown channel is dropped", async () => {
    storage({ "vg.alert-rules": { "slow-join": { on: false, to: ["email", "fax"] } } })
    const { body } = await get(`${base}/alert-setup`)
    expect(valid("AlertSetup", body)).toBe("")
    expect(body.items).toHaveLength(7)
    expect(body.items.find((r: { ruleId: string }) => r.ruleId === "slow-join")).toEqual({ ruleId: "slow-join", on: false, to: ["email"] })
    expect(body.items.find((r: { ruleId: string }) => r.ruleId === "cut-off")).toEqual({ ruleId: "cut-off", on: true, to: [] })
  })

  test("when storage reads but refuses writes, a save still comes back for this page view", async () => {
    storage({ "vg.thresholds": { answerMs: 900 } }, "writes")
    writeLocal("vg.thresholds", { answerMs: 2500, joinMs: 2000, ttsStartMs: 1000 })
    expect((await get(`${base}/settings`)).body.thresholds.answerMs).toBe(2500)
    // A write storage takes again ends the memory-only value.
    storage({})
    writeLocal("vg.thresholds", null)
  })

  test("two saves made before the read is answered again both land", async () => {
    storage({})
    const client = new QueryClient()
    const key = ["alert-setup-test"]
    client.setQueryData(key, (await get(`${base}/alert-setup`)).body)
    const save = localSave<Setup, AlertSetup>(client, "vg.alert-rules", key, (a) => (a ? setupFrom(a) : {}))
    void save((s) => ({ ...s, "slow-join": { on: false, to: [] } }))
    void save((s) => ({ ...s, "cut-off": { on: true, to: ["email"] } }))
    const { body } = await get(`${base}/alert-setup`)
    expect(body.items.find((r: { ruleId: string }) => r.ruleId === "slow-join").on).toBe(false)
    expect(body.items.find((r: { ruleId: string }) => r.ruleId === "cut-off").to).toEqual(["email"])
  })

  test("invitations are the organisation's, valid ones only, and an unknown organisation is not found", async () => {
    storage({ "vg.invites": [{ email: "a@example.com", role: "Viewer" }, { email: "not an email", role: "Admin" }, { email: "b@example.com", role: "Owner" }] })
    const { status, body } = await get("http://console.test/api/v1/org/default/invitations")
    expect(status).toBe(200)
    expect(valid("Invitations", body)).toBe("")
    expect(body.items).toEqual([{ email: "a@example.com", role: "Viewer" }])
    const other = await get("http://console.test/api/v1/org/other/invitations")
    expect(other.status).toBe(404)
    expect(valid("Error", other.body)).toBe("")
  })

  test("workspaces list the organisations and projects the mock answers for, and every settings read refuses another project", async () => {
    const { body } = await get("http://console.test/api/v1/workspaces")
    expect(valid("Workspaces", body)).toBe("")
    expect(body.items.flatMap((o: { id: string; projects: { id: string }[] }) => o.projects.map((p) => `${o.id}/${p.id}`))).toEqual(["default/default-project"])
    for (const path of ["settings", "issue-dispositions", "alert-setup"]) {
      const res = await get(`http://console.test/api/v1/org/default/project/elsewhere/${path}`)
      expect(res.status).toBe(404)
      expect(valid("Error", res.body)).toBe("")
    }
  })
})

describe("getRecordingMedia", () => {
  // A stand-in for the dev server's /captures folder, holding one recording.
  const audio = Buffer.from("OggS fake audio bytes for range tests")
  let origin = ""
  let files: http.Server
  beforeAll(async () => {
    files = http.createServer((req, res) => {
      if (req.url !== "/captures/RM_mVQFRQtyjpiJ.ogg") {
        res.writeHead(200, { "Content-Type": "text/html" }).end("<!doctype html>")
        return
      }
      const m = /bytes=(\d+)-(\d*)/.exec(req.headers.range ?? "")
      if (!m) {
        res.writeHead(200, { "Content-Type": "audio/ogg", "Content-Length": audio.length }).end(audio)
        return
      }
      const end = m[2] ? Number(m[2]) : audio.length - 1
      res.writeHead(206, { "Content-Type": "audio/ogg", "Content-Range": `bytes ${m[1]}-${end}/${audio.length}`, "Content-Length": end - Number(m[1]) + 1 }).end(audio.subarray(Number(m[1]), end + 1))
    })
    await new Promise<void>((done) => files.listen(0, "127.0.0.1", done))
    origin = `http://127.0.0.1:${(files.address() as AddressInfo).port}`
  })
  afterAll(() => {
    files.close()
  })
  const project = () => `${origin}/api/v1/org/default/project/default-project`

  test("a call's recording plays through the API in its own project, never from the captured file", async () => {
    for (const c of capturedCalls().filter((x) => x.recording && !x.recording.preview)) {
      const { body } = await get(`${base}/calls/${encodeURIComponent(c.id)}`)
      expect(body.recording.url).toBe(`/api/v1/org/default/project/default-project/recordings/${body.recording.id}`)
    }
  })

  test("serves the audio, and the byte range the player asks for", async () => {
    const whole = await fetch(`${project()}/recordings/RM_mVQFRQtyjpiJ`)
    expect(whole.status).toBe(200)
    expect(whole.headers.get("Content-Type")).toBe("audio/ogg")
    expect(whole.headers.get("Accept-Ranges")).toBe("bytes")
    expect(whole.headers.get("Cache-Control")).toContain("private")
    expect(Buffer.from(await whole.arrayBuffer())).toEqual(audio)
    const part = await fetch(`${project()}/recordings/RM_mVQFRQtyjpiJ`, { headers: { Range: "bytes=0-3" } })
    expect(part.status).toBe(206)
    expect(part.headers.get("Content-Range")).toBe(`bytes 0-3/${audio.length}`)
    expect(Buffer.from(await part.arrayBuffer()).toString()).toBe("OggS")
  })

  test.each([
    ["an id no capture names", "RM_unknown"],
    ["a path in place of an id", "..%2F..%2Fpackage.json"],
    ["a captured recording whose file is not on this machine", "RM_4rPUQqJUkbpr"],
  ])("%s is not found, never another file or the app's page", async (_name, id) => {
    const res = await fetch(`${project()}/recordings/${id}`)
    expect(res.status).toBe(404)
    expect(valid("Error", await res.json())).toBe("")
  })
})
