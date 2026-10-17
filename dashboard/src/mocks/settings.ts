import type { AlertRuleSetup, AlertSetup, Invitation, Invitations, IssueDisposition, IssueDispositions, ProjectSettings, Thresholds } from "@/lib/api-types"
import { readLocal } from "@/lib/local-store"

// The settings reads, served from what the console saved in this browser (lib/local-store.ts) until
// web stores them. Each read validates what it finds, as the server validates its own rows: a value
// that does not fit the contract is left out, never passed on.

// The contract's defaults (Thresholds in docs/api/openapi.yaml): configuration, not observations.
export const DEFAULT_THRESHOLDS: Thresholds = { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }

const record = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const positive = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : fallback)

// Each field falls back on its own, so one bad value does not discard the others.
export function storedThresholds(): Thresholds {
  const saved = record(readLocal("vg.thresholds"))
  return {
    answerMs: positive(saved.answerMs, DEFAULT_THRESHOLDS.answerMs),
    joinMs: positive(saved.joinMs, DEFAULT_THRESHOLDS.joinMs),
    ttsStartMs: positive(saved.ttsStartMs, DEFAULT_THRESHOLDS.ttsStartMs),
  }
}

// No bucket, retention or embed origin can be configured yet, so each is null: not configured,
// never a guessed default.
export const projectSettings = (): ProjectSettings => ({
  thresholds: storedThresholds(),
  storageBucket: null,
  transcriptRetentionDays: null,
  traceRetentionDays: null,
  recordingRetentionDays: null,
  allowedOrigins: null,
})

// Saved as { [issueId]: { state, at } } with at in milliseconds.
export function issueDispositions(): IssueDispositions {
  const items: IssueDisposition[] = []
  for (const [issueId, v] of Object.entries(record(readLocal("vg.issue-state")))) {
    const { state, at } = record(v)
    if ((state === "resolved" || state === "ignored") && typeof at === "number" && Number.isFinite(at)) items.push({ issueId, state, at: new Date(at).toISOString() })
  }
  return { items }
}

export const RULES: AlertRuleSetup["ruleId"][] = ["slow-answer", "cut-off", "job-failed", "slow-join", "plain-phone", "recording-lost", "ingress-error"]
const CHANNELS: AlertRuleSetup["to"] = ["slack", "teams", "discord", "pagerduty", "opsgenie", "email", "webhook"]

// Every rule, saved or not: a rule nobody changed is on and sends nowhere.
export function alertSetup(): AlertSetup {
  const saved = record(readLocal("vg.alert-rules"))
  return {
    items: RULES.map((ruleId) => {
      const { on, to } = record(saved[ruleId])
      return { ruleId, on: typeof on === "boolean" ? on : true, to: Array.isArray(to) ? CHANNELS.filter((c) => to.includes(c)) : [] }
    }),
  }
}

const ROLES = new Set<string>(["Admin", "Member", "Viewer"])
export function invitations(): Invitations {
  const saved = readLocal("vg.invites")
  const items: Invitation[] = []
  for (const v of Array.isArray(saved) ? saved : []) {
    const { email, role } = record(v)
    if (typeof email === "string" && /^[^\s@]+@[^\s@]+$/.test(email) && typeof role === "string" && ROLES.has(role) && !items.some((i) => i.email === email)) items.push({ email, role: role as Invitation["role"] })
  }
  return { items }
}
