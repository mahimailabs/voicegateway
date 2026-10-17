import type { AlertEvents, AlertSetup } from "@/lib/api-types"
import { alertSetupQuery, listCalls } from "@/lib/api"
import { useLocalSave } from "@/lib/local-store"
import { BUDGET_MS, JOIN_MS } from "@/lib/thresholds"
import egressRaw from "@/mocks/egress.json"
import ingressRaw from "@/mocks/ingress.json"
import sipRaw from "@/mocks/sip.json"
import { buildModel } from "../call/call-model"
import { TEST_AGENTS, agents, joinDelays } from "../agents/agents-model"
import { data as rooms } from "../media-server/room-drawer"
import { type SipData, checks as sipChecks, routes } from "../sip/sip-model"

// Alerts are the console's own checks with somewhere to send them (decision 0020). Each rule below
// reuses a check another page already runs, so an alert and a page never disagree, and each one is
// evaluated against the captured data to show what it would have fired on. Nothing is sent in
// Phase 1: sending runs in the Phase 2 worker.

// at is Unix seconds, null when the source reported no time.
export type Event = { at: number | null; where: string; detail: string; link?: { to: "/calls/$callId"; callId: string } | { to: "/calls" | "/sip" | "/egress" | "/ingress" | "/agents" } }
export type Rule = { id: string; title: string; condition: string; source: "Calls" | "Agents" | "Phone bridge" | "Egress" | "Ingress"; severity: "high" | "medium"; events: Event[]; state?: string }

// The server's computation, run by the mock API (mocks/insights.ts); pages not yet moved call it
// directly, which #323 removes.
export function rules(): Rule[] {
  const calls = listCalls().filter((c) => !c.recording?.preview)
  const slow: Event[] = calls.flatMap((c) => {
    const m = buildModel(c)
    const over = m.turns.filter((t) => t.responseMs !== null && t.responseMs > BUDGET_MS)
    return over.length ? [{ at: Date.parse(c.startedAt) / 1000, where: c.room, detail: `${over.length} of ${m.turns.length} turns over ${BUDGET_MS / 1000} s`, link: { to: "/calls/$callId" as const, callId: c.id } }] : []
  })
  const cutOff: Event[] = calls.flatMap((c) => {
    const n = (c.turns ?? []).filter((t) => t.interrupted).length
    return n ? [{ at: Date.parse(c.startedAt) / 1000, where: c.room, detail: `${n} ${n === 1 ? "reply" : "replies"} cut off by the caller`, link: { to: "/calls/$callId" as const, callId: c.id } }] : []
  })
  const all = agents()
  const failed: Event[] = all.flatMap((a) =>
    a.jobs.filter((j) => j.failed).map((j) => ({ at: Number.isFinite(j.dispatchedMs) ? j.dispatchedMs / 1000 : null, where: `${a.name ?? a.key} in ${j.room.name}`, detail: `${TEST_AGENTS.has(a.name ?? "") ? "Test data: our capture agent. " : ""}Job ${j.jobId} failed; LiveKit retried`, link: { to: "/agents" as const } })),
  )
  const slowJoin: Event[] = all.flatMap((a) =>
    a.jobs.flatMap((j) => {
      const d = joinDelays([j])[0]
      return d !== undefined && d * 1000 > JOIN_MS ? [{ at: Number.isFinite(j.dispatchedMs) ? j.dispatchedMs / 1000 : null, where: `${a.name ?? a.key} in ${j.room.name}`, detail: `Joined ${d.toFixed(1)} s after dispatch`, link: { to: "/agents" as const } }] : []
    }),
  )
  const sip = sipRaw as unknown as SipData
  const callRooms = rooms.rooms.filter((r) => r.participants.some((p) => p.kind === "sip"))
  const hints = callRooms.flatMap((r) => r.participants.filter((p) => p.sip).map((p) => ({ trunkId: p.sip!.trunkId, twilio: Boolean(p.sip!.twilio) })))
  const plain = sipChecks(sip, routes(sip, hints)).filter((c) => /not encrypted/i.test(c.title))
  const eg = (egressRaw as unknown as { egress: { id: string; room: string | null; status: string; error: string | null; endedAt: number | null; file: { sizeBytes: number; durationS: number } | null }[] }).egress
  const lost: Event[] = eg
    .filter((e) => e.status === "EGRESS_FAILED")
    .map((e) => ({ at: e.endedAt, where: `${e.id} in ${e.room ?? "unknown room"}`, detail: e.file?.durationS ? `${Math.round(e.file.durationS)} s of audio not stored` : (e.error ?? "Failed"), link: { to: "/egress" as const } }))
  const ing = (ingressRaw as unknown as { ingress: { id: string; name: string; status: string; error: string | null; endedAt: number | null }[] }).ingress
  const ingErr: Event[] = ing
    .filter((i) => i.status === "ENDPOINT_ERROR")
    .map((i) => ({ at: i.endedAt, where: `${i.name} (${i.id})`, detail: i.error ?? "Error", link: { to: "/ingress" as const } }))

  const events: Record<RuleId, Event[]> = { "slow-answer": slow, "cut-off": cutOff, "job-failed": failed, "slow-join": slowJoin, "plain-phone": [], "recording-lost": lost, "ingress-error": ingErr }
  return RULE_IDS.map((id) => ({ id, ...meta(id), events: events[id], state: id === "plain-phone" && plain.length ? `${plain.length} ${plain.length === 1 ? "trunk" : "trunks"} now` : undefined }))
}

// The seven rules (decision 0020): what each one is, which the page shows beside what the server
// evaluated. Conditions read the current thresholds.
export const RULE_IDS = ["slow-answer", "cut-off", "job-failed", "slow-join", "plain-phone", "recording-lost", "ingress-error"] as const
export type RuleId = (typeof RULE_IDS)[number]
export function meta(id: RuleId): Pick<Rule, "title" | "condition" | "source" | "severity"> {
  switch (id) {
    case "slow-answer":
      return { title: "Slow answers", condition: `A call has a turn over the ${BUDGET_MS / 1000} s answer time`, source: "Calls", severity: "medium" }
    case "cut-off":
      return { title: "Replies cut off", condition: "The caller talks over the agent and it stops", source: "Calls", severity: "medium" }
    case "job-failed":
      return { title: "Agent job failed", condition: "LiveKit reports an agent job as failed", source: "Agents", severity: "high" }
    case "slow-join":
      return { title: "Slow agent join", condition: `An agent joins more than ${JOIN_MS / 1000} s after dispatch`, source: "Agents", severity: "medium" }
    case "plain-phone":
      return { title: "Unencrypted phone leg", condition: "A SIP trunk carries audio as plain RTP", source: "Phone bridge", severity: "medium" }
    case "recording-lost":
      return { title: "Recording lost", condition: "An egress recording fails to upload", source: "Egress", severity: "high" }
    case "ingress-error":
      return { title: "Ingress error", condition: "An ingress endpoint goes into error", source: "Ingress", severity: "high" }
  }
}

// The page an event without a call links to.
const pageOf: Record<RuleId, "/calls" | "/agents" | "/sip" | "/egress" | "/ingress"> = {
  "slow-answer": "/calls",
  "cut-off": "/calls",
  "job-failed": "/agents",
  "slow-join": "/agents",
  "plain-phone": "/sip",
  "recording-lost": "/egress",
  "ingress-error": "/ingress",
}

// The page's view of listAlertEvents: each rule's description with what it would have fired on.
export function rulesFrom(ev: AlertEvents): Rule[] {
  return ev.rules.map((r) => ({
    id: r.ruleId,
    ...meta(r.ruleId),
    state: r.state ?? undefined,
    events: r.events.map((e) => ({
      at: e.at === null ? null : Date.parse(e.at) / 1000,
      where: e.where,
      detail: e.detail,
      link: e.callId ? { to: "/calls/$callId" as const, callId: e.callId } : { to: pageOf[r.ruleId] },
    })),
  }))
}

// Which rules are on and where each one goes: getAlertSetup reads it; saving goes to this browser
// until the server stores it.
export const CHANNELS = ["slack", "teams", "discord", "pagerduty", "opsgenie", "email", "webhook"] as const
export type Channel = (typeof CHANNELS)[number]
export const channelName: Record<Channel, string> = { slack: "Slack", teams: "Microsoft Teams", discord: "Discord", pagerduty: "PagerDuty", opsgenie: "Opsgenie", email: "Email", webhook: "Webhook" }
export type Setup = Record<string, { on: boolean; to: Channel[] }>
export const setupFrom = (a: AlertSetup): Setup => Object.fromEntries(a.items.map((r) => [r.ruleId, { on: r.on, to: r.to }]))
export function useSetRule() {
  const save = useLocalSave<Setup, AlertSetup>("vg.alert-rules", alertSetupQuery().queryKey, (a) => (a ? setupFrom(a) : {}))
  return (id: string, change: Partial<{ on: boolean; to: Channel[] }>) => save((current) => ({ ...current, [id]: { ...ruleSetup(current, id), ...change } }))
}
export const ruleSetup = (s: Setup, id: string) => s[id] ?? { on: true, to: [] as Channel[] }
