import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import type { AlertChannel, AlertEvents, AlertIncident, AlertSetup } from "@/lib/api-types"
import { alertSetupQuery, saveAlertRule } from "@/lib/api"
import { errorText } from "@/components/page-state"
import { BUDGET_MS, JOIN_MS } from "@/lib/thresholds"

// Alerts are the console's own checks with somewhere to send them (decision 0020). Each rule below
// reuses a check another page already runs, so an alert and a page never disagree, and the server
// evaluates each one (mocks/insights.ts in the mock) to show what it would have fired on. The worker
// groups what a rule matches into incidents and sends them to the rule's channels.

// at is Unix seconds, null when the source reported no time.
export type Event = { at: number | null; where: string; detail: string; link?: { to: "/calls/$callId"; callId: string } | { to: "/calls" | "/sip" | "/egress" | "/ingress" | "/agents" } }
export type Rule = { id: string; title: string; condition: string; source: "Calls" | "Agents" | "Phone bridge" | "Egress" | "Ingress"; severity: "high" | "medium"; events: Event[]; state?: string }

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

// Which rules are on and where each one goes, from getAlertSetup; updateAlertRule saves a change.
// Email and webhook deliver today; the other kinds are listed as coming, and the server refuses them.
export const CHANNELS = ["slack", "teams", "discord", "pagerduty", "opsgenie", "email", "webhook"] as const
export type Channel = (typeof CHANNELS)[number]
export const DELIVERING = ["email", "webhook"] as const satisfies readonly Channel[]
export type Delivering = (typeof DELIVERING)[number]
export const delivers = (c: Channel): c is Delivering => (DELIVERING as readonly Channel[]).includes(c)
export const channelName: Record<Channel, string> = { slack: "Slack", teams: "Microsoft Teams", discord: "Discord", pagerduty: "PagerDuty", opsgenie: "Opsgenie", email: "Email", webhook: "Webhook" }
export type Setup = Record<string, { on: boolean; to: Channel[] }>
export const setupFrom = (a: AlertSetup): Setup => Object.fromEntries(a.items.map((r) => [r.ruleId, { on: r.on, to: r.to }]))
export const ruleSetup = (s: Setup, id: string) => s[id] ?? { on: true, to: [] as Channel[] }

// Saves one rule, showing the change at once and putting the saved setup back if the server refuses.
export function useSetRule() {
  const qc = useQueryClient()
  const key = alertSetupQuery().queryKey
  const save = useMutation({
    mutationFn: (v: { id: RuleId; on: boolean; to: Delivering[] }) => saveAlertRule(v.id, { on: v.on, to: v.to }),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: key })
      const prior = qc.getQueryData<AlertSetup>(key)
      if (prior) qc.setQueryData<AlertSetup>(key, { ...prior, items: prior.items.map((r) => (r.ruleId === v.id ? { ...r, on: v.on, to: v.to } : r)) })
      return { prior }
    },
    onError: (e: Error, _v, ctx) => {
      if (ctx?.prior) qc.setQueryData(key, ctx.prior)
      toast.error(`Could not save the rule: ${errorText(e)}`)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  })
  return (s: Setup, id: RuleId, change: Partial<{ on: boolean; to: Channel[] }>) => {
    const next = { ...ruleSetup(s, id), ...change }
    save.mutate({ id, on: next.on, to: next.to.filter(delivers) })
  }
}

// The firing incidents of each rule.
export function firingByRule(list: AlertIncident[]): Partial<Record<RuleId, AlertIncident[]>> {
  const out: Partial<Record<RuleId, AlertIncident[]>> = {}
  for (const i of list) if (i.state === "firing") (out[i.ruleId] ??= []).push(i)
  return out
}

// A channel whose last delivery failed or that a refusal marked broken, named for the rule cards.
export const failing = (channels: AlertChannel[]) => channels.filter((c) => c.broken || c.lastDelivery?.state === "failed")
