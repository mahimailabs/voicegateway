import type { Call, Span } from "@/lib/api"

// Export order is not ancestry order. Walk ids, then order measured spans on their own clock.
export const orderedSpans = (spans: Span[]) => [...spans].sort((a, b) => (a.startMs ?? Infinity) - (b.startMs ?? Infinity) || a.spanId.localeCompare(b.spanId))

export function descendants(spans: Span[], root: Span | undefined): Span[] {
  if (!root) return []
  const seen = new Set([root.spanId])
  const pending = [root.spanId]
  const result: Span[] = []
  while (pending.length) {
    const id = pending.pop()
    for (const s of spans) {
      if (s.parentId !== id || seen.has(s.spanId) || s.clockId !== root.clockId) continue
      seen.add(s.spanId)
      pending.push(s.spanId)
      result.push(s)
    }
  }
  return orderedSpans(result)
}

export function turnSpans(call: Call, i: number) {
  const spans = call.spans ?? []
  const turns = orderedSpans(spans.filter((s) => s.name === "agent_turn"))
  const id = call.turns?.[i]?.spanId
  const root = id ? turns.find((s) => s.spanId === id) : turns[i]
  return { root, children: descendants(spans, root) }
}

export function userTurnFor(call: Call, agent: Span | undefined) {
  if (agent?.startMs == null) return { turn: undefined, children: [] as Span[] }
  const spans = orderedSpans((call.spans ?? []).filter((s) => s.clockId === agent.clockId && s.startMs !== null))
  const prev = spans.filter((s) => s.name === "agent_turn" && s.startMs! < agent.startMs!).at(-1)
  const turn = spans.filter((s) => s.name === "user_turn" && s.startMs! < agent.startMs! && (prev?.startMs == null || s.startMs! >= prev.startMs)).at(-1)
  return { turn, children: descendants(spans, turn) }
}

export function numericAttr(s: Span | undefined, key: string, sentinel?: number): number | null {
  const raw = s?.attrs?.[key]
  if (raw == null || raw.trim() === "") return null
  const n = Number(raw)
  return Number.isFinite(n) && n !== sentinel ? n : null
}
