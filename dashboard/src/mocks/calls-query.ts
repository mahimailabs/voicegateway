import type { CallSummary, CallTotals } from "@/lib/api-types"
import { BUDGET_MS } from "@/lib/thresholds"

// listCalls' range, filters, sort and totals, as docs/api/openapi.yaml defines them, for the mock
// API. An unknown value never matches a bound and is only matched by `unknown`.

type Channel = CallSummary["channel"]
type Severity = "high" | "medium" | "none"
type SortKey = "startedAt" | "durationSeconds" | "turnCount" | "answerMs" | "issues"

export type CallFilters = {
  from?: number
  to?: number
  room?: string
  channel?: Channel[]
  issues?: Severity[]
  answerMsMin?: number
  answerMsMax?: number
  durationSecondsMin?: number
  durationSecondsMax?: number
  turnsMin?: number
  turnsMax?: number
  unknown?: ("answerMs" | "durationSeconds" | "turnCount")[]
  tag?: string[]
  recorded?: boolean
  archived?: boolean
  sort: SortKey
  order: "asc" | "desc"
}

const CHANNELS: Channel[] = ["phone", "web", "console", "local", "unknown"]
const SEVERITIES: Severity[] = ["high", "medium", "none"]
const SORTS: SortKey[] = ["startedAt", "durationSeconds", "turnCount", "answerMs", "issues"]
const UNKNOWNS = ["answerMs", "durationSeconds", "turnCount"] as const

// Reads the query string; returns the problem as text when a value is not what the contract allows.
export function parseFilters(q: URLSearchParams): CallFilters | string {
  const f: CallFilters = { sort: "startedAt", order: "desc" }
  const time = (k: "from" | "to") => {
    const v = q.get(k)
    if (v === null) return undefined
    const t = Date.parse(v)
    if (Number.isNaN(t)) throw `${k} must be a date-time.`
    return t
  }
  const num = (k: string, integer = false) => {
    const v = q.get(k)
    if (v === null) return undefined
    const n = Number(v)
    if (v === "" || !Number.isFinite(n) || n < 0 || (integer && !Number.isInteger(n))) throw `${k} must be a ${integer ? "whole " : ""}number of 0 or more.`
    return n
  }
  const list = <T extends string>(k: string, allowed?: readonly T[]) => {
    const vs = q.getAll(k)
    if (!vs.length) return undefined
    if (allowed) for (const v of vs) if (!allowed.includes(v as T)) throw `${k} has an unknown value ${v}.`
    return vs as T[]
  }
  try {
    f.from = time("from")
    f.to = time("to")
    f.room = q.get("room") ?? undefined
    f.channel = list("channel", CHANNELS)
    f.issues = list("issues", SEVERITIES)
    f.answerMsMin = num("answerMsMin")
    f.answerMsMax = num("answerMsMax")
    f.durationSecondsMin = num("durationSecondsMin")
    f.durationSecondsMax = num("durationSecondsMax")
    f.turnsMin = num("turnsMin", true)
    f.turnsMax = num("turnsMax", true)
    f.unknown = list("unknown", UNKNOWNS)
    f.tag = list("tag")
    for (const k of ["recorded", "archived"] as const) {
      const v = q.get(k)
      if (v !== null && v !== "true" && v !== "false") throw `${k} must be true or false.`
      if (v !== null) f[k] = v === "true"
    }
    const sort = q.get("sort")
    if (sort !== null && !SORTS.includes(sort as SortKey)) throw "Unknown sort."
    if (sort) f.sort = sort as SortKey
    const order = q.get("order")
    if (order !== null && order !== "asc" && order !== "desc") throw "order must be asc or desc."
    if (order) f.order = order
  } catch (problem) {
    return String(problem)
  }
  return f
}

// The highest severity, or null when the call had nothing to check.
export function severity(c: CallSummary): Severity | null {
  const { high, medium } = c.issues
  if (high === null || medium === null) return null
  return high ? "high" : medium ? "medium" : "none"
}

const within = (v: number | null, min?: number, max?: number) => (min === undefined && max === undefined ? true : v !== null && (min === undefined || v >= min) && (max === undefined || v <= max))

const valueOf: Record<(typeof UNKNOWNS)[number], (c: CallSummary) => number | null> = {
  answerMs: (c) => c.answerMsMedian,
  durationSeconds: (c) => c.durationSeconds,
  turnCount: (c) => c.turnCount,
}

export function matchesFilters(c: CallSummary, f: CallFilters): boolean {
  const started = Date.parse(c.startedAt)
  if (f.from !== undefined && started < f.from) return false
  if (f.to !== undefined && started >= f.to) return false
  if (f.room !== undefined && c.room !== f.room) return false
  if (f.channel && !f.channel.includes(c.channel)) return false
  if (f.issues) {
    const s = severity(c)
    if (s === null || !f.issues.includes(s)) return false
  }
  if (!within(c.answerMsMedian, f.answerMsMin, f.answerMsMax)) return false
  if (!within(c.durationSeconds, f.durationSecondsMin, f.durationSecondsMax)) return false
  if (!within(c.turnCount, f.turnsMin, f.turnsMax)) return false
  if (f.unknown && !f.unknown.every((k) => valueOf[k](c) === null)) return false
  if (f.tag && !f.tag.some((t) => c.tags.includes(t))) return false
  if (f.recorded !== undefined && c.recorded !== f.recorded) return false
  if (f.archived !== undefined && c.archived !== f.archived) return false
  return true
}

const sortValue: Record<SortKey, (c: CallSummary) => number | null> = {
  startedAt: (c) => Date.parse(c.startedAt),
  durationSeconds: (c) => c.durationSeconds,
  turnCount: (c) => c.turnCount,
  answerMs: (c) => c.answerMsMedian,
  issues: (c) => (c.issues.high === null || c.issues.medium === null ? null : c.issues.high * 1000 + c.issues.medium),
}

// Unknown values sort after known ones in both orders; ties break by id.
export function sortCalls(calls: CallSummary[], f: CallFilters): CallSummary[] {
  const get = sortValue[f.sort]
  const dir = f.order === "asc" ? 1 : -1
  return [...calls].sort((a, b) => {
    const x = get(a)
    const y = get(b)
    if (x === null || y === null) return x === y ? a.id.localeCompare(b.id) : x === null ? 1 : -1
    return x === y ? a.id.localeCompare(b.id) : (x - y) * dir
  })
}

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}

// The most answer times a response carries for the distribution strip.
const MAX_VALUES = 1000

export function totals(calls: CallSummary[]): CallTotals {
  const byChannel = Object.fromEntries(CHANNELS.map((ch) => [ch, calls.filter((c) => c.channel === ch).length])) as CallTotals["byChannel"]
  // Newest calls first, so a cut drops the oldest.
  const newest = [...calls].sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt))
  const all = newest.flatMap((c) => c.answerMs ?? [])
  const sev = calls.map(severity)
  return {
    callCount: calls.length,
    byChannel,
    answers: {
      turns: all.length,
      overBudget: all.filter((a) => a > BUDGET_MS).length,
      budgetMs: BUDGET_MS,
      p50Ms: median(all),
      valuesMs: all.slice(0, MAX_VALUES),
      valuesTruncated: all.length > MAX_VALUES,
    },
    issues: { high: sev.filter((s) => s === "high").length, medium: sev.filter((s) => s === "medium").length, none: sev.filter((s) => s === "none").length, unknown: sev.filter((s) => s === null).length },
    recorded: calls.filter((c) => c.recorded).length,
  }
}
