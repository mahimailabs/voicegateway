import type { AlertChannel, AlertRuleSetup, AlertRuleUpdate, AlertSetup, SavedAlertChannel, CreatedInvitation, CreatedPasswordReset, CreatedProjectKey, Invitation, Invitations, Member, Members, ProjectKey, ProjectKeys, ProjectSettings, Thresholds } from "@/lib/api-types"

// The settings reads, served from what the console saved in this browser (lib/local-store.ts) until
// web stores them. Each read validates what it finds, as the server validates its own rows: a value
// that does not fit the contract is left out, never passed on.

// The contract's defaults (Thresholds in docs/api/openapi.yaml): configuration, not observations.
export const DEFAULT_THRESHOLDS: Thresholds = { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }

const record = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const settingsByProject = new Map<string, ProjectSettings>()
const defaultScope = "default/default-project"
export function resetProjectSettings() { settingsByProject.clear() }
export const projectSettings = (scope = defaultScope): ProjectSettings => structuredClone(settingsByProject.get(scope) ?? {
  thresholds: DEFAULT_THRESHOLDS, revision: 0, storageBucket: null,
  transcriptRetentionDays: null, traceRetentionDays: null, recordingRetentionDays: null, allowedOrigins: null, allowedOriginsRevision: 0,
})
export const storedThresholds = (scope = defaultScope): Thresholds => projectSettings(scope).thresholds
export function updateSettings(scope: string, input: unknown): ProjectSettings | "invalid_request" | "revision_conflict" {
  const value = record(input)
  const thresholds = record(value.thresholds)
  if (Object.keys(value).some((k) => !["thresholds", "revision"].includes(k)) || Object.keys(thresholds).some((k) => !["answerMs", "joinMs", "ttsStartMs"].includes(k)) || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || Number(value.revision) >= Number.MAX_SAFE_INTEGER ||
      ![thresholds.answerMs, thresholds.joinMs, thresholds.ttsStartMs].every((v) => typeof v === "number" && Number.isFinite(v) && v >= 100 && v <= 60000)) return "invalid_request"
  const prior = projectSettings(scope)
  if (value.revision !== prior.revision) return "revision_conflict"
  const saved = { ...prior, thresholds: thresholds as Thresholds, revision: prior.revision + 1 }
  settingsByProject.set(scope, structuredClone(saved))
  return saved
}

// Allowed embed origins as web normalises them (internal/api/embeds.go), or null for a value web refuses.
export function normaliseOrigin(v: unknown): string | null {
  if (typeof v !== "string") return null
  let u: URL
  try { u = new URL(v.trim()) } catch { return null }
  if (u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "") || u.hostname.includes("*")) return null
  const local = u.hostname === "localhost" || u.hostname === "127.0.0.1" || u.hostname === "[::1]"
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local)) return null
  return u.origin
}
export function updateAllowedOrigins(scope: string, input: unknown): ProjectSettings | "invalid_request" | "revision_conflict" {
  const value = record(input)
  if (Object.keys(value).some((k) => !["allowedOrigins", "allowedOriginsRevision"].includes(k)) || !Number.isSafeInteger(value.allowedOriginsRevision)) return "invalid_request"
  const list = value.allowedOrigins === null ? [] : Array.isArray(value.allowedOrigins) ? value.allowedOrigins.map(normaliseOrigin) : null
  if (!list || list.length > 20 || list.some((o) => o === null)) return "invalid_request"
  const prior = projectSettings(scope)
  if (value.allowedOriginsRevision !== prior.allowedOriginsRevision) return "revision_conflict"
  const origins = [...new Set(list as string[])]
  const saved = { ...prior, allowedOrigins: origins.length ? origins : null, allowedOriginsRevision: prior.allowedOriginsRevision + 1 }
  settingsByProject.set(scope, structuredClone(saved))
  return saved
}

export const RULES: AlertRuleSetup["ruleId"][] = ["slow-answer", "cut-off", "job-failed", "slow-join", "plain-phone", "recording-lost", "ingress-error"]

// Alert rules and channels, held in memory for the mock as web holds them in Postgres: a rule nobody
// changed is on and sends nowhere; a channel keeps only what web would answer, never a URL or secret.
const ruleState = new Map<string, { on: boolean; to: AlertRuleUpdate["to"] }>()
const channelState = new Map<AlertChannel["kind"], AlertChannel>()
export function resetAlerts() {
  ruleState.clear()
  channelState.clear()
}
export const alertSetup = (): AlertSetup => ({
  items: RULES.map((ruleId) => ({ ruleId, ...(ruleState.get(ruleId) ?? { on: true, to: [] }) })),
  channels: [...channelState.values()].sort((a, b) => a.kind.localeCompare(b.kind)),
  emailAvailable: true,
})
export const alertChannels = () => [...channelState.keys()]

/** Saves a rule as web does: only email and webhook, de-duplicated and sorted. */
export function updateAlertRule(ruleId: string, input: unknown): AlertRuleSetup | "not_found" | "invalid_request" {
  if (!RULES.includes(ruleId as AlertRuleSetup["ruleId"])) return "not_found"
  const v = record(input)
  if (Object.keys(v).some((k) => !["on", "to"].includes(k)) || typeof v.on !== "boolean" || !Array.isArray(v.to) || v.to.some((c) => c !== "email" && c !== "webhook")) return "invalid_request"
  const to = [...new Set(v.to as AlertRuleUpdate["to"])].sort()
  ruleState.set(ruleId, { on: v.on, to })
  return { ruleId: ruleId as AlertRuleSetup["ruleId"], on: v.on, to }
}

const secret = () => `whsec_${btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))}`

/** Sets a channel as web does; the webhook's secret is made on create or rotate and answered once. */
export function updateAlertChannel(kind: string, input: unknown): SavedAlertChannel | "not_found" | "invalid_request" {
  if (kind !== "email" && kind !== "webhook") return "not_found"
  const v = record(input)
  if (Object.keys(v).some((k) => !["url", "rotateSecret", "recipients"].includes(k))) return "invalid_request"
  const now = new Date().toISOString()
  if (kind === "email") {
    const list = Array.isArray(v.recipients) ? [...new Set(v.recipients.map((x) => String(x).trim().toLowerCase()))] : []
    if (v.url !== undefined || !list.length || list.length > 20 || list.some((x) => !/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(x))) return "invalid_request"
    const target = `${list.length} ${list.length === 1 ? "recipient" : "recipients"}`
    channelState.set("email", { kind, target, recipients: list, broken: false, updatedAt: now, lastDelivery: channelState.get("email")?.lastDelivery ?? null })
    return { kind, target, recipients: list, signingSecret: null }
  }
  let host = ""
  try {
    const u = new URL(String(v.url))
    host = ["http:", "https:"].includes(u.protocol) ? u.hostname : ""
  } catch {
    host = ""
  }
  if (!host || v.recipients !== undefined) return "invalid_request"
  const fresh = !channelState.has("webhook") || v.rotateSecret === true
  channelState.set("webhook", { kind, target: host, recipients: null, broken: false, updatedAt: now, lastDelivery: channelState.get("webhook")?.lastDelivery ?? null })
  return { kind, target: host, recipients: null, signingSecret: fresh ? secret() : null }
}
export const deleteAlertChannel = (kind: string) => channelState.delete(kind as AlertChannel["kind"])

/** Queues a test as web does; the mock has no worker, so it is delivered at once. */
export function testAlertChannel(kind: string): boolean {
  const c = channelState.get(kind as AlertChannel["kind"])
  if (!c) return false
  const now = new Date().toISOString()
  c.lastDelivery = { id: crypto.randomUUID(), channel: c.kind, change: "test", state: "delivered", attempts: 1, nextAttemptAt: null, status: c.kind === "email" ? "sent" : "HTTP 200", message: null, deliveredAt: now, createdAt: now }
  return true
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
