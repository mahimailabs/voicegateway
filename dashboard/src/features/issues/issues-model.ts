import { listCalls } from "@/lib/api"
import sipRaw from "@/mocks/sip.json"
import { buildModel } from "../call/call-model"
import { findingsFor } from "../call/call-steps"
import { agents, checks as agentChecks } from "../agents/agents-model"
import { data as rooms } from "../media-server/room-drawer"
import { type SipData, checks as sipChecks, routes } from "../sip/sip-model"

// Issues are the findings the console already computes, grouped across calls and components
// (decision 0021). Nothing new is measured here: each group is the same check the call drawer, the
// Agent workers page or the Phone bridge page runs, so the counts always match those pages.

export type Severity = "high" | "medium" | "low" | "info"
export type Hit = { at: number | null; where: string; detail: string; callId?: string }
export type Issue = { id: string; title: string; severity: Severity; source: "Calls" | "Agent workers" | "Phone bridge"; to: "/calls" | "/agents" | "/sip"; hits: Hit[] }

// One name per call finding, so "3 of 9 replies were cut off" in two calls becomes one issue.
const kinds: Record<string, string> = {
  eot: "Waited for the end of the turn",
  cut: "Replies cut off by the caller",
  bc: "Backchannel answered as a turn",
  slow: "Slow answers",
  late: "Transcript arrived late",
  discard: "Model calls thrown away",
  zero: "Model reported 0 tokens",
  tts: "Slow start of speech",
  stall: "Agent process stalled",
  rt: "No stage timings (realtime model)",
}
const rank: Record<Severity, number> = { high: 0, medium: 1, low: 2, info: 3 }

export function issues(): Issue[] {
  const byKind = new Map<string, Issue>()
  for (const c of listCalls().filter((x) => !x.recording?.preview)) {
    const at = Date.parse(c.startedAt) / 1000
    for (const f of findingsFor(c, buildModel(c))) {
      const kind = f.id.replace(/\d+$/, "")
      const i = byKind.get(kind) ?? { id: kind, title: kinds[kind] ?? f.title, severity: f.severity, source: "Calls" as const, to: "/calls" as const, hits: [] }
      if (rank[f.severity] < rank[i.severity]) i.severity = f.severity
      i.hits.push({ at: Number.isFinite(at) ? at : null, where: c.room, detail: f.title, callId: c.id })
      byKind.set(kind, i)
    }
  }
  const fromChecks = (source: Issue["source"], to: Issue["to"], list: { severity: Severity; title: string; where: string; why: string }[]) => {
    const m = new Map<string, Issue>()
    for (const c of list) {
      const i = m.get(c.title) ?? { id: `${to}:${c.title}`, title: c.title, severity: c.severity, source, to, hits: [] }
      i.hits.push({ at: null, where: c.where, detail: c.why })
      m.set(c.title, i)
    }
    return [...m.values()]
  }
  const sip = sipRaw as unknown as SipData
  const hints = rooms.rooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) })))
  return [...byKind.values(), ...fromChecks("Agent workers", "/agents", agentChecks(agents())), ...fromChecks("Phone bridge", "/sip", sipChecks(sip, routes(sip, hints)))].sort(
    (a, b) => rank[a.severity] - rank[b.severity] || b.hits.length - a.hits.length,
  )
}
