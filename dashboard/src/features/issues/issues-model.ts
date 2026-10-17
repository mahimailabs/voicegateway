import type { IssueList } from "@/lib/api-types"

// Issues are the findings the console already computes, grouped across calls and components
// (decision 0021). Nothing new is measured: the server groups the same checks the call drawer, the
// Agent workers page and the Phone bridge page run (mocks/insights.ts in the mock), so the counts
// match those pages.
//
// A call finding is an event: each hit is dated by its call and a range keeps the hits inside it.
// An Agent workers or Phone bridge check is standing: it is true of the latest read (a trunk that
// accepts any source, failed jobs in what ListDispatch lists now), its hits carry no time, and it
// shows as current state in any range, never as a problem in it.

export type Severity = "high" | "medium" | "low" | "info"
export type Hit = { at: number | null; where: string; detail: string; callId?: string }
export type Kind = "event" | "standing"
export type Issue = { id: string; title: string; kind: Kind; severity: Severity; source: "Calls" | "Agent workers" | "Phone bridge"; to: "/calls" | "/agents" | "/sip"; hits: Hit[] }

// The page's view of listIssues: the source as its label and page, hit times as Unix seconds.
const sourceView = {
  calls: { label: "Calls", to: "/calls" },
  agent_workers: { label: "Agent workers", to: "/agents" },
  phone_bridge: { label: "Phone bridge", to: "/sip" },
} as const
export function issuesFrom(list: IssueList): Issue[] {
  return list.items.map((i) => ({
    id: i.id,
    title: i.title,
    kind: i.kind,
    severity: i.severity,
    source: sourceView[i.source].label,
    to: sourceView[i.source].to,
    hits: i.hits.map((h) => ({ at: h.at === null ? null : Date.parse(h.at) / 1000, where: h.where, detail: h.detail, callId: h.callId ?? undefined })),
  }))
}
