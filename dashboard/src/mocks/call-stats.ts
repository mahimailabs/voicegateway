import type { CallStats, StageTiming } from "@/lib/api-types"
import type { Call } from "@/lib/api"
import { BUDGET_MS } from "@/lib/thresholds"
import { buildModel, median } from "@/features/call/call-model"
import { stageDefs } from "@/features/overview/visuals"
import { waterfallRows } from "@/features/profile/ttfa-waterfall"
import { capturedCalls } from "./fixtures"
import { issueList } from "./insights"
import { summarize } from "./summaries"

// What the server will compute for Overview (getCallStats), here in the mock: the Calls list's own
// per-call figures (summarize) and the call drawer's turn model, totalled over every call in the range.

const MAX_VALUES = 5000
const MAX_CALLS = 500
const CHANNELS = ["phone", "web", "console", "local", "unknown"] as const

const p90 = (xs: number[]) => (xs.length ? [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.ceil(xs.length * 0.9) - 1)] : null)

// Turn anatomy over a set of calls: per stage, the median start, the median end and where the slow
// tail ends (median start plus the 90th percentile length), each over the turns that reported it.
function anatomy(calls: Call[]): { stages: StageTiming[]; turns: number } {
  const by = new Map<string, { s: number[]; e: number[] }>()
  const rt: number[] = []
  let turns = 0
  for (const c of calls) {
    const m = buildModel(c)
    for (const t of m.turns) {
      if (t.responseMs === null && t.userText === null) continue
      turns++
      if (m.pipeline === "realtime") {
        if (t.responseMs !== null) rt.push(t.responseMs)
        continue
      }
      for (const r of waterfallRows(c, t) ?? []) {
        const key = r.label === "LLM" ? "LLM, call 1" : r.label
        const v = by.get(key) ?? { s: [], e: [] }
        v.s.push(r.start)
        v.e.push(r.end)
        by.set(key, v)
      }
    }
  }
  const stages: StageTiming[] = stageDefs
    .filter((d) => by.has(d.key))
    .map((d) => {
      const v = by.get(d.key)!
      const start = median(v.s)!
      const end = Math.max(median(v.e)!, start + 1)
      return { stage: d.key, field: d.field, startMs: start, medianMs: end, p90Ms: Math.max(start + p90(v.e.map((e, i) => e - v.s[i]))!, end), turns: v.s.length }
    })
  if (rt.length) stages.push({ stage: "Realtime", field: "realtime ttft, no stage breakdown", startMs: 0, medianMs: median(rt), p90Ms: p90(rt), turns: rt.length })
  return { stages, turns }
}

export function callStats(from: Date, to: Date): CallStats {
  const calls = capturedCalls()
    .filter((c) => !c.recording?.preview)
    .filter((c) => Date.parse(c.startedAt) >= +from && Date.parse(c.startedAt) < +to)
  const rows = calls.map((c) => ({ c, s: summarize(c), m: buildModel(c) }))
  // Most recent call first, so a cut keeps the newest turns.
  const newest = [...rows].sort((a, b) => Date.parse(b.c.startedAt) - Date.parse(a.c.startedAt))
  const values = newest.flatMap(({ c, s, m }) => m.turns.filter((t) => t.responseMs !== null).map((t) => ({ ms: t.responseMs, callId: c.id, room: c.room, turn: t.i, agentKey: s.agentKey })))
  const all = newest.flatMap(({ s }) => s.answerMs ?? [])

  const days: CallStats["byDay"] = []
  for (let d = Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate()); d < +to; d += 86_400_000) {
    const date = new Date(d).toISOString().slice(0, 10)
    days.push({ date, callCount: calls.filter((c) => c.startedAt.slice(0, 10) === date).length })
  }

  const found = issueList(from, to).items
  const count = (sev: string) => found.filter((i) => i.severity === sev).length
  const overall = anatomy(calls)

  const keys = [...new Set(rows.map(({ s }) => s.agentKey))]
  const agents: CallStats["agents"] = keys
    .map((key) => {
      const mine = rows.filter(({ s }) => s.agentKey === key)
      const shape = anatomy(mine.map(({ c }) => c))
      const checked = mine.filter(({ s }) => s.issues.high !== null || s.issues.medium !== null)
      const oldest = [...mine].sort((a, b) => Date.parse(a.c.startedAt) - Date.parse(b.c.startedAt))
      return {
        key,
        name: key === "unnamed" ? null : key,
        callCount: mine.length,
        answerP50Ms: median(mine.flatMap(({ s }) => s.answerMs ?? [])),
        // Unknown when none of the calls had anything to check, not zero.
        issues: checked.length ? checked.reduce((n, { s }) => n + (s.issues.high ?? 0) + (s.issues.medium ?? 0), 0) : null,
        models: [...new Set(mine.flatMap(({ s }) => s.models))],
        anatomyTurns: shape.turns,
        stages: shape.stages,
        calls: oldest.slice(-MAX_CALLS).map(({ c, s }) => ({ callId: c.id, room: c.room, answerP50Ms: s.answerMsMedian })),
        callsTruncated: oldest.length > MAX_CALLS,
      }
    })
    .sort((a, b) => (b.callCount ?? 0) - (a.callCount ?? 0))

  return {
    from: from.toISOString(),
    to: to.toISOString(),
    complete: true,
    callCount: calls.length,
    byChannel: Object.fromEntries(CHANNELS.map((ch) => [ch, rows.filter(({ s }) => s.channel === ch).length])) as CallStats["byChannel"],
    byDay: days,
    answers: { turns: all.length, overBudget: all.filter((a) => a > BUDGET_MS).length, budgetMs: BUDGET_MS, p50Ms: median(all), p90Ms: p90(all), values: values.slice(0, MAX_VALUES), valuesTruncated: values.length > MAX_VALUES },
    issueCounts: { high: count("high"), medium: count("medium"), low: count("low"), info: count("info") },
    anatomyTurns: overall.turns,
    stages: overall.stages,
    agents,
  }
}
