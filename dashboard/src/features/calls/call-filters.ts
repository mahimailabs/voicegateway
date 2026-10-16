import { flattenFilterConditions } from "@/components/reui/filters/filters-query"
import type { FilterOperator, FilterQuery } from "@/components/reui/filters/filters-types"

// The Calls filters run on the server (listCalls), so each field offers only what the contract can
// express, and the chips become its query parameters. Anything the server cannot express is named
// and left out, never applied in the browser behind its back.

const op = (value: string, label: string, arity: FilterOperator["arity"] = "one"): FilterOperator => ({ value, label, arity })

export const serverOperators = {
  room: [op("is", "is")],
  choice: [op("is", "is"), op("is_any_of", "is any of", "many")],
  // A complete list of values, so "is not" is the rest of the list.
  choiceWithNot: [op("is", "is"), op("is_not", "is not"), op("is_any_of", "is any of", "many"), op("is_none_of", "is none of", "many")],
  number: [op("eq", "is"), op("gte", "at least"), op("lte", "at most"), op("between", "between", "range"), op("empty", "is unknown", "none")],
  boolean: [op("is", "is")],
  tags: [op("has_any_of", "has any of", "many")],
} satisfies Record<string, FilterOperator[]>

const CHANNELS = ["phone", "web", "console", "local", "unknown"]
const bounds: Record<string, { min: string; max: string; unknown: string }> = {
  answer: { min: "answerMsMin", max: "answerMsMax", unknown: "answerMs" },
  length: { min: "durationSecondsMin", max: "durationSecondsMax", unknown: "durationSeconds" },
  turns: { min: "turnsMin", max: "turnsMax", unknown: "turnCount" },
}

export type ServerFilter = { params: URLSearchParams; dropped: string[] }

export function toServerParams(query: FilterQuery, labels: Record<string, string>): ServerFilter {
  const params = new URLSearchParams()
  const dropped: string[] = []
  const conditions = flattenFilterConditions(query)
  if (query.rules.some((r) => r.type === "group")) dropped.push("grouped filters")
  if (query.combinator === "or" && conditions.length > 1) {
    dropped.push(`"or" between filters (the server matches every filter)`)
    return { params, dropped }
  }
  const lists = new Map<string, Set<string>>()
  const narrow = (key: string, values: string[]) => {
    const prev = lists.get(key)
    lists.set(key, new Set(prev ? values.filter((v) => prev.has(v)) : values))
  }
  const min = new Map<string, number>()
  const max = new Map<string, number>()
  for (const c of conditions) {
    const name = labels[c.field] ?? c.field
    const vs = c.values.map(String)
    if (c.negated) {
      dropped.push(`"not" on ${name}`)
      continue
    }
    if (c.field === "room" && c.operator === "is") params.append("room", vs[0])
    else if ((c.field === "channel" || c.field === "issues") && ["is", "is_any_of"].includes(c.operator)) narrow(c.field, vs)
    else if (c.field === "channel" && ["is_not", "is_none_of"].includes(c.operator)) narrow("channel", CHANNELS.filter((x) => !vs.includes(x)))
    else if (c.field in bounds) {
      const b = bounds[c.field]
      const n = vs.map(Number)
      if (c.operator === "empty") params.append("unknown", b.unknown)
      else if (c.operator === "eq") (min.set(b.min, Math.max(min.get(b.min) ?? n[0], n[0])), max.set(b.max, Math.min(max.get(b.max) ?? n[0], n[0])))
      else if (c.operator === "gte") min.set(b.min, Math.max(min.get(b.min) ?? n[0], n[0]))
      else if (c.operator === "lte") max.set(b.max, Math.min(max.get(b.max) ?? n[0], n[0]))
      else if (c.operator === "between") (min.set(b.min, Math.max(min.get(b.min) ?? n[0], n[0])), max.set(b.max, Math.min(max.get(b.max) ?? n[1], n[1])))
      else dropped.push(`${name} ${c.operator}`)
    } else if (c.field === "recorded" && c.operator === "is") params.set("recorded", String(vs[0] === "true"))
    else if (c.field === "tag" && c.operator === "has_any_of") narrow("tag", vs)
    else dropped.push(`${name} ${c.operator}`)
  }
  for (const [key, values] of lists) {
    // Values that rule each other out match nothing; the server is told so rather than shown nothing.
    if (!values.size) params.append("room", "")
    for (const v of values) params.append(key === "tag" ? "tag" : key, v)
  }
  for (const [k, v] of min) params.set(k, String(v))
  for (const [k, v] of max) params.set(k, String(v))
  return { params, dropped }
}
