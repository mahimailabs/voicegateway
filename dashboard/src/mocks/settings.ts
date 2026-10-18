import type { AlertRuleSetup, AlertSetup, CreatedInvitation, CreatedPasswordReset, CreatedProjectKey, Invitation, Invitations, Member, Members, ProjectKey, ProjectKeys, IssueDisposition, IssueDispositions, ProjectSettings, Thresholds } from "@/lib/api-types"
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

// The organisation's members, held in memory for the mock: changes last until the page reloads.
let memberList: Member[] = [
  { userId: "u-ada", email: "ada@example.com", role: "Admin" },
  { userId: "u-bob", email: "bob@example.com", role: "Member" },
  { userId: "u-cat", email: "cat@example.com", role: "Viewer" },
]
export const members = (): Members => ({ items: [...memberList].sort((a, b) => a.email.localeCompare(b.email)) })

/** Changes or removes a member as web does, refusing to leave no admin. */
export function changeMember(userId: string, role: Member["role"] | null): Member | "not_found" | "last_admin" | null {
  const m = memberList.find((x) => x.userId === userId)
  if (!m) return "not_found"
  if (m.role === "Admin" && role !== "Admin" && memberList.filter((x) => x.role === "Admin").length === 1) return "last_admin"
  if (role === null) {
    memberList = memberList.filter((x) => x.userId !== userId)
    return null
  }
  memberList = memberList.map((x) => (x.userId === userId ? { ...x, role } : x))
  return { ...m, role }
}

// Project API keys, held in memory for the mock: the full key is made here and shown once, as web
// does, and only its list fields are kept.
const keyStore = new Map<string, ProjectKey[]>()
const b32 = "abcdefghijklmnopqrstuvwxyz234567"
const draw = (n: number, alphabet: string) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => alphabet[b % alphabet.length]).join("")
export const projectKeys = (project: string): ProjectKeys => ({ items: [...(keyStore.get(project) ?? [])].reverse() })
export function createKey(project: string, name: string): CreatedProjectKey {
  const keyId = draw(12, b32)
  const key: ProjectKey = { keyId, name, prefix: `vgk_${keyId}`, createdAt: new Date().toISOString(), createdBy: null, lastUsedAt: null, revokedAt: null }
  keyStore.set(project, [...(keyStore.get(project) ?? []), key])
  return { ...key, secret: `vgk_${keyId}_${draw(43, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")}` }
}
/** Revokes a key, keeping the first revocation; false when the key is not this project's. */
export function revokeKey(project: string, keyId: string): boolean {
  const list = keyStore.get(project) ?? []
  if (!list.some((k) => k.keyId === keyId)) return false
  keyStore.set(project, list.map((k) => (k.keyId === keyId && k.revokedAt === null ? { ...k, revokedAt: new Date().toISOString() } : k)))
  return true
}

// Invitations and reset links, in memory, as web without SMTP answers them: each create returns
// its link once, and only a hash-like id is kept.
let invitationList: Invitation[] = []
const DAY = 24 * 60 * 60 * 1000
const linkFor = () => `${globalThis.location?.origin ?? "http://localhost"}/invite#${draw(43, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")}`

export const invitations = (): Invitations => ({ items: [...invitationList].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).map((i) => ({ ...i, state: i.state === "pending" && Date.parse(i.expiresAt) <= Date.now() ? "expired" : i.state })) })

// Replaces the open link for the same email and kind, as web does.
function addLink(kind: Invitation["kind"], email: string, role: Invitation["role"], lifetime: number): { invitation: Invitation; link: string } {
  invitationList = invitationList.map((i) => (i.email === email && i.kind === kind && i.state === "pending" ? { ...i, state: "revoked" } : i))
  const now = new Date()
  const invitation: Invitation = { id: crypto.randomUUID(), kind, email, role, state: "pending", createdAt: now.toISOString(), expiresAt: new Date(now.getTime() + lifetime).toISOString(), invitedBy: "ada@example.com" }
  invitationList = [...invitationList, invitation]
  return { invitation, link: linkFor() }
}

export function invite(email: string, role: Member["role"]): CreatedInvitation | "already_member" {
  const clean = email.toLowerCase()
  if (memberList.some((m) => m.email === clean)) return "already_member"
  const { invitation, link } = addLink("invite", clean, role, 7 * DAY)
  return { invitation, link, emailed: false }
}

export function passwordReset(userId: string): CreatedPasswordReset | "not_found" {
  const m = memberList.find((x) => x.userId === userId)
  if (!m) return "not_found"
  const { invitation, link } = addLink("reset", m.email, null, DAY)
  return { link, expiresAt: invitation.expiresAt, emailed: false }
}

/** Revokes a link, keeping an accepted one accepted; false when there is no such link. */
export function revokeInvitation(id: string): boolean {
  if (!invitationList.some((i) => i.id === id)) return false
  invitationList = invitationList.map((i) => (i.id === id && i.state === "pending" ? { ...i, state: "revoked" } : i))
  return true
}
