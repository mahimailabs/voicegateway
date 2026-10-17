import type { AlertEvents, IssueList } from "@/lib/api-types"
import { BUDGET_MS, JOIN_MS } from "@/lib/thresholds"
import { buildModel } from "@/features/call/call-model"
import { findingsFor } from "@/features/call/call-steps"
import { TEST_AGENTS, agentsFrom, checks as agentChecks, joinDelays } from "@/features/agents/agents-model"
import { type Event, RULE_IDS, type Rule, type RuleId, meta } from "@/features/alerts/alerts-model"
import type { Issue, Severity } from "@/features/issues/issues-model"
import { fromContract } from "@/features/media-server/room-drawer"
import { checks as sipChecks, routes } from "@/features/sip/sip-model"
import { capturedCalls } from "./fixtures"
import { cloudAgents, egress, ingress, mediaServer, sip } from "./livekit"

// What the server will compute for the Issues and Alerts pages, here in the mock: the console's own
// checks over the reads the mock serves (calls, CloudAgent, media server, SIP, egress, ingress), in
// the contract's shape. Times are Unix seconds while the checks run and RFC 3339 in the answer; an
// event with no time stays null and is kept whatever the range.

const iso = (s: number | null) => (s === null || !Number.isFinite(s) || s <= 0 ? null : new Date(s * 1000).toISOString())
const secs = (t: string | null) => (t === null ? null : Date.parse(t) / 1000)
const inRange = (s: number | null, from: number, to: number) => s === null || !Number.isFinite(s) || s <= 0 || (s * 1000 >= from && s * 1000 < to)
const source: Record<string, IssueList["items"][number]["source"]> = { Calls: "calls", "Agent workers": "agent_workers", "Phone bridge": "phone_bridge" }

const calls = () => capturedCalls().filter((c) => !c.recording?.preview)
const rooms = () => fromContract(mediaServer())
const agents = () => agentsFrom(cloudAgents(), rooms())
// The trunk each phone caller came through, for the SIP checks' provider hints.
const sipHints = () => rooms().rooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) })))

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

// Every issue group with all its hits: the call drawer's findings by kind, then the Agent workers
// and Phone bridge checks by title.
export function issues(): Issue[] {
  const byKind = new Map<string, Issue>()
  for (const c of calls()) {
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
  const s = sip()
  return [...byKind.values(), ...fromChecks("Agent workers", "/agents", agentChecks(agents())), ...fromChecks("Phone bridge", "/sip", sipChecks(s, routes(s, sipHints())))].sort(
    (a, b) => rank[a.severity] - rank[b.severity] || b.hits.length - a.hits.length,
  )
}

// The seven rules, each with every event it would have fired on.
export function rules(): Rule[] {
  const all = calls()
  const slow: Event[] = all.flatMap((c) => {
    const m = buildModel(c)
    const over = m.turns.filter((t) => t.responseMs !== null && t.responseMs > BUDGET_MS)
    return over.length ? [{ at: Date.parse(c.startedAt) / 1000, where: c.room, detail: `${over.length} of ${m.turns.length} turns over ${BUDGET_MS / 1000} s`, link: { to: "/calls/$callId" as const, callId: c.id } }] : []
  })
  const cutOff: Event[] = all.flatMap((c) => {
    const n = (c.turns ?? []).filter((t) => t.interrupted).length
    return n ? [{ at: Date.parse(c.startedAt) / 1000, where: c.room, detail: `${n} ${n === 1 ? "reply" : "replies"} cut off by the caller`, link: { to: "/calls/$callId" as const, callId: c.id } }] : []
  })
  const list = agents()
  const failed: Event[] = list.flatMap((a) =>
    a.jobs.filter((j) => j.failed).map((j) => ({ at: Number.isFinite(j.dispatchedMs) ? j.dispatchedMs / 1000 : null, where: `${a.name ?? a.key} in ${j.room.name}`, detail: `${TEST_AGENTS.has(a.name ?? "") ? "Test data: our capture agent. " : ""}Job ${j.jobId} failed; LiveKit retried`, link: { to: "/agents" as const } })),
  )
  const slowJoin: Event[] = list.flatMap((a) =>
    a.jobs.flatMap((j) => {
      const d = joinDelays([j])[0]
      return d !== undefined && d * 1000 > JOIN_MS ? [{ at: Number.isFinite(j.dispatchedMs) ? j.dispatchedMs / 1000 : null, where: `${a.name ?? a.key} in ${j.room.name}`, detail: `Joined ${d.toFixed(1)} s after dispatch`, link: { to: "/agents" as const } }] : []
    }),
  )
  const s = sip()
  const plain = sipChecks(s, routes(s, sipHints())).filter((c) => /not encrypted/i.test(c.title))
  const lost: Event[] = egress()
    .egress.filter((e) => e.status === "EGRESS_FAILED")
    .map((e) => ({ at: secs(e.endedAt), where: `${e.id} in ${e.room ?? "unknown room"}`, detail: e.file?.durationS ? `${Math.round(e.file.durationS)} s of audio not stored` : (e.error ?? "Failed"), link: { to: "/egress" as const } }))
  const ingErr: Event[] = ingress()
    .ingress.filter((i) => i.status === "ENDPOINT_ERROR")
    .map((i) => ({ at: secs(i.endedAt), where: `${i.name} (${i.id})`, detail: i.error ?? "Error", link: { to: "/ingress" as const } }))

  const events: Record<RuleId, Event[]> = { "slow-answer": slow, "cut-off": cutOff, "job-failed": failed, "slow-join": slowJoin, "plain-phone": [], "recording-lost": lost, "ingress-error": ingErr }
  return RULE_IDS.map((id) => ({ id, ...meta(id), events: events[id], state: id === "plain-phone" && plain.length ? `${plain.length} ${plain.length === 1 ? "trunk" : "trunks"} now` : undefined }))
}

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
