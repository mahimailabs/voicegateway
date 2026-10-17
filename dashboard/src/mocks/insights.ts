import type { AlertEvents, IssueList } from "@/lib/api-types"
import { issues } from "@/features/issues/issues-model"
import { rules } from "@/features/alerts/alerts-model"

// What the server will compute for the Issues and Alerts pages, here in the mock: the console's own
// checks (issues-model, alerts-model) over the captured data, in the contract's shape. Unix seconds
// become RFC 3339; an event with no time stays null and is kept whatever the range.

const iso = (s: number | null) => (s === null || !Number.isFinite(s) || s <= 0 ? null : new Date(s * 1000).toISOString())
const inRange = (s: number | null, from: number, to: number) => s === null || !Number.isFinite(s) || s <= 0 || (s * 1000 >= from && s * 1000 < to)
const source: Record<string, IssueList["items"][number]["source"]> = { Calls: "calls", "Agent workers": "agent_workers", "Phone bridge": "phone_bridge" }

export function issueList(from: Date, to: Date): IssueList {
  const items = issues()
    .map((i) => {
      const hits = i.hits.filter((h) => inRange(h.at, +from, +to))
      const times = hits.flatMap((h) => (h.at !== null && Number.isFinite(h.at) && h.at > 0 ? [h.at] : []))
      return {
        id: i.id,
        title: i.title,
        severity: i.severity,
        source: source[i.source],
        lastSeenAt: times.length ? iso(Math.max(...times)) : null,
        // Most recent first, as the contract orders them; hits with no time last.
        hits: [...hits].sort((a, b) => (b.at ?? -Infinity) - (a.at ?? -Infinity)).map((h) => ({ at: iso(h.at), where: h.where, detail: h.detail, callId: h.callId ?? null })),
      }
    })
    .filter((i) => i.hits.length > 0)
  return { from: from.toISOString(), to: to.toISOString(), complete: true, items }
}

export function alertEvents(from: Date, to: Date): AlertEvents {
  return {
    from: from.toISOString(),
    to: to.toISOString(),
    complete: true,
    rules: rules().map((r) => ({
      ruleId: r.id as AlertEvents["rules"][number]["ruleId"],
      state: r.state ?? null,
      events: r.events
        .filter((e) => inRange(e.at, +from, +to))
        .sort((a, b) => (b.at ?? -Infinity) - (a.at ?? -Infinity))
        .map((e) => ({ at: iso(e.at), where: e.where, detail: e.detail, callId: e.link && "callId" in e.link ? e.link.callId : null })),
    })),
  }
}
