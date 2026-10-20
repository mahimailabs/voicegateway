import type { IssueList, IssueMetadata } from "@/lib/api-types"

// Issues are the findings the console already computes, grouped across calls and components
// (decision 0021). Nothing new is measured: the server groups the same checks the call drawer, the
// Agent workers page and the Phone bridge page run (mocks/insights.ts in the mock), so the counts
// match those pages.
//
// A call finding is an event: each hit is dated by its call and a range keeps the hits inside it.
// Component findings are standing and appear in every range. A separate observation time and
// evidence state distinguish current findings from preserved stale or unavailable evidence.

export type Severity = "high" | "medium" | "low" | "info"
export type Hit = { observedAt: number | null; evidenceState: IssueList["items"][number]["hits"][number]["evidenceState"]; at: number | null; where: string; detail: string; callId?: string }
export type Kind = "event" | "standing"
export type Issue = { id: string; title: string; kind: Kind; severity: Severity; source: "Calls" | "Agent workers" | "Phone bridge"; to: "/calls" | "/agents" | "/sip"; hits: Hit[]; metadata: IssueMetadata }

// The page's view of listIssues: the source as its label and page, hit times as Unix seconds.
const sourceView = {
  calls: { label: "Calls", to: "/calls" },
  agent_workers: { label: "Agent workers", to: "/agents" },
  phone_bridge: { label: "Phone bridge", to: "/sip" },
} as const
export function issuesFrom(list: IssueList): Issue[] {
  return list.items.map((i) => ({
    id: i.id,
    metadata: i.metadata,
    title: i.title,
    kind: i.kind,
    severity: i.severity,
    source: sourceView[i.source].label,
    to: sourceView[i.source].to,
    hits: i.hits.map((h) => ({ observedAt:h.observedAt === null ? null : Date.parse(h.observedAt)/1000,evidenceState:h.evidenceState,at: h.at === null ? null : Date.parse(h.at) / 1000, where: h.where, detail: h.detail, callId: h.callId ?? undefined })),
  }))
}

// The latest dated hit, or null when no hit has a time.
export const lastSeen = (i: Issue) => {
  const t = Math.max(...i.hits.map((h) => h.at ?? -Infinity))
  return Number.isFinite(t) ? t : null
}

// Confirmed current findings lead; older component evidence uses its last observation time.
// An event with no reported time stays at the far end.
export const standingState = (i: Issue) => {
  for (const state of ["unavailable", "stale", "unknown"] as const) if (i.hits.some(h => h.evidenceState === state)) return state
  return i.hits.length > 0 && i.hits.every(h => h.evidenceState === "current") ? "current" : "unknown"
}
export const evidenceLabel = { recorded:"Recorded",current:"Current",stale:"Stale",unavailable:"Source unavailable",unknown:"Not confirmed" } as const
export const lastSortKey = (i: Issue) => i.kind === "standing"
  ? standingState(i) === "current" ? Infinity : Math.max(...i.hits.map(h => h.observedAt ?? -Infinity))
  : lastSeen(i) ?? -Infinity

export function coverageSummary(coverage: IssueList["coverage"]): string {
  const parts: string[] = []
  if (coverage.pendingCalls === null) parts.push("Call evaluation status unknown")
  if (coverage.unavailableCalls === null) parts.push("Call evidence availability unknown")
  if (coverage.pendingJobs === null) parts.push("Job evaluation status unknown")
  if (coverage.pendingCalls) parts.push(`${coverage.pendingCalls} calls pending evaluation`)
  if (coverage.unavailableCalls) parts.push(`${coverage.unavailableCalls} calls with unavailable evidence`)
  if (coverage.pendingJobs) parts.push(`${coverage.pendingJobs} job observations pending evaluation`)
  const labels = { media_server: "LiveKit SFU", sip: "SIP", cloud_agents: "Cloud agents", egress: "Egress", ingress: "Ingress" }
  for (const source of coverage.sources) {
    if (source.state !== "current") parts.push(`${labels[source.layer]}: ${source.state}`)
  }
  return parts.join(" · ")
}
