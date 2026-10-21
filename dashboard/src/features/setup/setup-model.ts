import { humanId } from "human-id"
import type { Call } from "@/lib/api"
import type { OrganisationEntitlement, ProjectConnection } from "@/lib/api-types"
import { buildModel, interruptionCount, turnCount } from "@/features/call/call-model"
import { fixText, sourceState } from "@/features/connection/connection-model"

// The Cloud setup journey: Welcome (0), Connect (1), Agent (2), Call (3), First look (4). The step
// comes from what the API answered, never from browser state, so leaving and coming back resumes:
//   no project: 1 (Welcome is shown first until the user moves on)
//   a project, the agent's upload (or the SDK) never accepted: 2
//   the agent connected, no call stored: 3
//   the first call stored: 4
// The webhook is optional here, so it does not move the journey. An unread connection is step 2.
export type SetupStep = 0 | 1 | 2 | 3 | 4

export const RAIL: { step: 1 | 2 | 3 | 4; label: string }[] = [
  { step: 1, label: "Connect" },
  { step: 2, label: "Agent" },
  { step: 3, label: "Call" },
  { step: 4, label: "First look" },
]

export const agentConnected = (c: ProjectConnection, now: number) => [c.agent, c.sdk].some((s) => sourceState(s, now).state === "connected")

export function setupStep(hasProject: boolean, c: ProjectConnection | undefined, now: number): 1 | 2 | 3 | 4 {
  if (!hasProject) return 1
  if (!c) return 2
  if (c.firstCall) return 4
  return agentConnected(c, now) ? 3 : 2
}

// The screen on view: a step the URL asks for (?step=), never one ahead of the data. With no project
// and nothing asked, Welcome.
export function viewedStep(derived: 1 | 2 | 3 | 4, asked: unknown): SetupStep {
  const n = typeof asked === "number" ? asked : asked === null || asked === undefined ? Number.NaN : Number(asked)
  if (Number.isInteger(n) && n >= 0 && n <= derived) return n as SetupStep
  return derived === 1 ? 0 : derived
}

// The project's name: its LiveKit Cloud subdomain (mahimai-kan8nzy4 for
// wss://mahimai-kan8nzy4.livekit.cloud), or the first label of another host with a subdomain; a
// generated readable name when the URL has none (an IP address, localhost, a bare domain).
export function projectName(url: string, fallback: () => string = () => humanId({ separator: "-", capitalize: false })): string {
  try {
    const host = new URL(url.trim()).hostname.toLowerCase()
    const labels = host.split(".")
    const first = labels[0]
    const usable = labels.length >= 3 && first !== "www" && /[a-z]/.test(first) && !/^\d+$/.test(labels[labels.length - 1])
    return usable ? first.slice(0, 64) : fallback()
  } catch {
    return fallback()
  }
}

// The LiveKit project as people know it: the subdomain of a LiveKit Cloud URL, else the host.
export function projectLabel(url: string): string {
  try {
    const host = new URL(url.trim()).host
    return host.endsWith(".livekit.cloud") ? host.slice(0, -".livekit.cloud".length) : host
  } catch {
    return url.trim()
  }
}

// A key is not secret, but the card shows only its start.
export const maskedKey = (key: string) => `${key.slice(0, 5)}${"•".repeat(Math.max(4, Math.min(10, key.length - 5)))}`

// The third check: the live room count from the verify read. Null means not counted: "Connected".
export function roomsLine(liveRooms: number | null): string {
  if (liveRooms === null) return "Connected"
  if (liveRooms === 0) return "No rooms live right now"
  return `${liveRooms} ${liveRooms === 1 ? "room" : "rooms"} live right now`
}

// The first look at the first call, from getCall with the call view's own model, so the numbers match
// the call page. The first turn with a measured answer time is the one shown. Unknown stays null.
export type Stage = { name: string; ms: number }
export type FirstLook = {
  answerMs: number | null
  answerDerived: boolean
  turn: number | null
  stages: Stage[]
  slowest: Stage | null
  turns: number | null
  interruptions: number | null
}

export function firstLook(call: Call): FirstLook {
  const model = buildModel(call)
  const t = model.turns.find((x) => x.responseMs !== null) ?? null
  const stages = t
    ? [
        { name: "end of turn detection", ms: t.eotMs },
        { name: "the LLM's first token", ms: t.ttftMs },
        { name: "text to speech starting", ms: t.ttsMs },
      ].filter((s): s is Stage => s.ms !== null)
    : []
  const slowest = stages.reduce<Stage | null>((a, s) => (a === null || s.ms > a.ms ? s : a), null)
  const turns = turnCount(call, model)
  return { answerMs: t?.responseMs ?? null, answerDerived: t?.responseDerived ?? false, turn: t ? t.i + 1 : null, stages, slowest, turns, interruptions: turns === null ? null : interruptionCount(model) }
}

// "{used} of {limit} min", as the usage allowance counts it; null when there is no entitlement read.
export const planText = (e: OrganisationEntitlement | undefined) => (e ? `${Math.floor(e.callSeconds / 60).toLocaleString()} of ${e.limitMinutes.toLocaleString()} min` : null)

export const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`)

// The agent refusals in the words of someone setting up: a key from another LiveKit project is
// the common one. Other reasons keep the Connect panel's wording (connection-model.ts fixText).
// summary is the one line shown; details, behind Details, adds what the summary does not say, and is
// null when there is nothing more.
export function agentProblem(c: ProjectConnection): { title: string; summary: string; details: string | null } | null {
  if (c.agent.reason === "key_mismatch") {
    const used = c.agent.usedKey ? c.agent.usedKey : "a key VoiceGateway could not read"
    const expected = c.agent.expectedKey ?? c.livekit.apiKey
    return {
      title: "Your agent uses a different LiveKit API key from this project's.",
      summary: "Use this project's key and secret in your agent, then restart it.",
      details: `Your agent signed with ${used}; this project expects ${expected}. Set LIVEKIT_API_KEY to ${expected} and LIVEKIT_API_SECRET to its secret.`,
    }
  }
  const f = fixText("agent", c.agent)
  const summary = `${f.fix.split(". ")[0].replace(/\.$/, "")}.`
  return { title: f.title, summary, details: f.fix === summary ? null : f.fix }
}
