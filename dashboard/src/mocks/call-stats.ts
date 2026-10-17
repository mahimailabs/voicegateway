import type { CallStats, ChainStage, ProviderUse, StageTiming } from "@/lib/api-types"
import type { Call } from "@/lib/api"
import { storedThresholds } from "./settings"
import { providerOfModel } from "@/components/provider-logo"
import { buildModel, median, placed } from "@/features/call/call-model"
import { stageDefs } from "@/features/overview/visuals"
import { waterfallRows } from "@/features/profile/ttfa-waterfall"
import { capturedCalls } from "./fixtures"
import { issueList } from "./insights"
import { providerProbes } from "./livekit"
import { summarize } from "./summaries"

// What the server will compute for Overview (getCallStats), here in the mock: the Calls list's own
// per-call figures (summarize) and the call drawer's turn model, totalled over every call in the range.

const MAX_VALUES = 5000
const MAX_CALLS = 500
const MAX_HOURS = 168
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
      for (const r of waterfallRows(c, t, providerProbes()) ?? []) {
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

// Every stage of each turn as its own duration, the way LiveKit reports it; a stage a turn did not
// report is left out of that stage's median, never counted as zero.
type ChainId = ChainStage["stage"]
function chain(calls: Call[]): ChainStage[] {
  const by = new Map<ChainId, number[]>()
  const add = (id: ChainId, v: number | null | undefined) => {
    if (v === null || v === undefined) return
    by.set(id, [...(by.get(id) ?? []), v])
  }
  for (const c of calls) {
    const m = buildModel(c)
    const spans = (c.spans ?? []).filter(placed)
    for (const t of m.turns) {
      if (t.userText === null && t.responseMs === null) continue
      const within = (start: number) => start / 1000 >= t.window.start && start / 1000 <= t.window.end
      if (m.pipeline === "realtime") add("realtime", t.responseMs)
      else {
        const rows = waterfallRows(c, t, providerProbes()) ?? []
        const dur = (label: string) => {
          const r = rows.find((x) => x.label === label)
          return r ? r.end - r.start : undefined
        }
        add("stt", dur("Speech to text") ?? c.turns?.[t.i]?.transcriptionDelayMs)
        add("eou", dur("Turn detection"))
        add("eot", t.eotMs)
        add("llm1", dur("LLM") ?? dur("LLM, call 1") ?? t.ttftMs)
        add("llm2", dur("LLM, call 2"))
        const tools = rows.filter((r) => r.label === "Tool")
        if (tools.length) add("tool", tools.reduce((a, r) => a + (r.end - r.start), 0))
        add("tts", dur("Text to speech") ?? t.ttsMs)
        add("hook", spans.find((x) => x.name === "on_user_turn_completed" && within(x.startMs))?.durationMs)
      }
      const stalls = spans.filter((x) => x.name === "event_loop_blocked" && within(x.startMs))
      if (stalls.length) add("stall", Math.max(...stalls.map((x) => Number(x.attrs?.["lk.blocking.duration"] ?? x.durationMs / 1000) * 1000)))
    }
  }
  const order: ChainId[] = ["stt", "eou", "eot", "hook", "llm1", "tool", "llm2", "tts", "realtime", "stall"]
  return order.filter((id) => by.has(id)).map((id) => ({ stage: id, medianMs: median(by.get(id)!), maxMs: Math.max(...by.get(id)!), turns: by.get(id)!.length }))
}

// The providers an agent's turns used, from the model each stage's span names.
function providers(calls: Call[]): ProviderUse[] {
  type Role = ProviderUse["roles"][number]["role"]
  const by = new Map<string, { calls: Set<string>; roles: Map<Role, number[]> }>()
  const add = (prov: string | null, role: Role, callId: string, ms: number) => {
    if (!prov) return
    const v = by.get(prov) ?? { calls: new Set<string>(), roles: new Map<Role, number[]>() }
    v.calls.add(callId)
    v.roles.set(role, [...(v.roles.get(role) ?? []), ms])
    by.set(prov, v)
  }
  for (const c of calls) {
    const m = buildModel(c)
    for (const t of m.turns) {
      if (m.pipeline === "realtime") {
        if (t.responseMs !== null) add(providerOfModel(m.models.find((x) => x.includes("realtime")) ?? m.models[0]), "realtime", c.id, t.responseMs)
        continue
      }
      for (const w of waterfallRows(c, t, providerProbes()) ?? []) {
        const model = w.sub.replace(/ (first token|first audio)$/, "")
        if (w.label === "Speech to text") add(providerOfModel(model), "stt", c.id, w.end - w.start)
        else if (w.label === "LLM" || w.label === "LLM, call 1") add(providerOfModel(model), "llm", c.id, w.end - w.start)
        else if (w.label === "Text to speech") add(providerOfModel(model), "tts", c.id, w.end - w.start)
      }
    }
  }
  const roles: Role[] = ["stt", "llm", "tts", "realtime"]
  return [...by].map(([provider, v]) => ({ provider, callCount: v.calls.size, roles: roles.filter((r) => v.roles.has(r)).map((role) => ({ role, medianMs: median(v.roles.get(role)!), turns: v.roles.get(role)!.length })) }))
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

  const perDay = new Map<string, number>()
  for (const c of calls) {
    const date = new Date(c.startedAt).toISOString().slice(0, 10)
    perDay.set(date, (perDay.get(date) ?? 0) + 1)
  }
  const days: CallStats["byDay"] = []
  for (let d = Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate()); d < +to; d += 86_400_000) {
    const date = new Date(d).toISOString().slice(0, 10)
    days.push({ date, callCount: perDay.get(date) ?? 0 })
  }

  // The last 168 UTC hours of the range at most; calls are bucketed once, not rescanned per hour.
  const HOUR = 3_600_000
  const byHourKey = new Map<number, typeof rows>()
  for (const r of rows) {
    const h = Math.floor(Date.parse(r.c.startedAt) / HOUR) * HOUR
    byHourKey.set(h, [...(byHourKey.get(h) ?? []), r])
  }
  const hours: CallStats["byHour"] = []
  for (let h = Math.max(Math.floor(+from / HOUR), Math.ceil(+to / HOUR) - MAX_HOURS) * HOUR; h < +to; h += HOUR) {
    const inHour = byHourKey.get(h) ?? []
    hours.push({ hour: new Date(h).toISOString(), callCount: inHour.length, answerP50Ms: median(inHour.flatMap(({ s }) => s.answerMs ?? [])) })
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
        answerTurns: mine.flatMap(({ s }) => s.answerMs ?? []).length,
        answerP50Ms: median(mine.flatMap(({ s }) => s.answerMs ?? [])),
        // Unknown when none of the calls had anything to check, not zero.
        issues: checked.length ? checked.reduce((n, { s }) => n + (s.issues.high ?? 0) + (s.issues.medium ?? 0), 0) : null,
        models: [...new Set(mine.flatMap(({ s }) => s.models))],
        providers: providers(mine.map(({ c }) => c)),
        anatomyTurns: shape.turns,
        stages: shape.stages,
        chain: chain(mine.map(({ c }) => c)),
        calls: oldest.slice(-MAX_CALLS).map(({ c, s }) => ({ callId: c.id, room: c.room, answerP50Ms: s.answerMsMedian })),
        callsTruncated: oldest.length > MAX_CALLS,
      }
    })
    .sort((a, b) => (b.callCount ?? 0) - (a.callCount ?? 0))

  const budget = storedThresholds().answerMs
  return {
    from: from.toISOString(),
    to: to.toISOString(),
    complete: true,
    callCount: calls.length,
    byChannel: Object.fromEntries(CHANNELS.map((ch) => [ch, rows.filter(({ s }) => s.channel === ch).length])) as CallStats["byChannel"],
    byDay: days,
    byHour: hours,
    answers: { turns: all.length, overBudget: all.filter((a) => a > budget).length, budgetMs: budget, p50Ms: median(all), p90Ms: p90(all), values: values.slice(0, MAX_VALUES), valuesTruncated: values.length > MAX_VALUES },
    issueCounts: { high: count("high"), medium: count("medium"), low: count("low"), info: count("info") },
    anatomyTurns: overall.turns,
    stages: overall.stages,
    chain: chain(calls),
    agents,
  }
}
