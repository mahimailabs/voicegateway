import type { Call, CallSummary } from "@/lib/api"
import { buildModel } from "@/features/call/call-model"
import { findingsFor } from "@/features/call/call-steps"
import type { MediaServerData } from "@/features/media-server/room-drawer"
import mediaServer from "@/mocks/media-server.json"
import { isArchived, tagsOf } from "./dispositions"

// What the server computes for the Calls list, here in the mock until web serves it. The checks
// are the call drawer's own (buildModel, findingsFor), so a row and its drawer never disagree.

const rooms = (mediaServer as unknown as MediaServerData).rooms

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}

// How a call reached the agent. Rooms we watched say it from their participants; console and local
// test sessions say it from where they were captured; with no evidence it is unknown.
function channelOf(c: Call): CallSummary["channel"] {
  if (c.recording?.preview) return "local"
  const r = rooms.find((x) => x.sid === c.roomSid)
  if (r?.participants.some((p) => p.kind === "sip")) return "phone"
  if (r) return "web"
  if (c.room.startsWith("console")) return "console"
  if (c.room.startsWith("vg-sip")) return "phone"
  // The spike's rooms were local test sessions; anything else has no evidence either way.
  return c.room.startsWith("spike") ? "local" : "unknown"
}

// The agent that took the call: the agent in its watched room, else the one its own trace names
// (lk.agent_name on its spans). Console and local test sessions name none.
export function agentKeyOf(c: Call): string | null {
  const a = rooms.find((x) => x.sid === c.roomSid)?.participants.find((p) => p.kind === "agent")
  if (a) return a.agentName ?? "unnamed"
  const named = (c.spans ?? []).map((sp) => sp.attrs?.["lk.agent_name"]).find((n) => n)
  return typeof named === "string" ? named : null
}

// Agent turns from the spans when the call has them, else the reported turns; unknown when neither.
const turnCount = (c: Call) => (c.spans ? c.spans.filter((s) => s.name === "agent_turn").length : (c.turns?.length ?? null))

export function summarize(c: Call): CallSummary {
  const model = buildModel(c)
  const answers = model.turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
  const findings = findingsFor(c, model)
  // Nothing to check: the counts are unknown, not zero.
  const checked = Boolean(c.spans?.length || c.turns?.length || c.events?.length)
  return {
    id: c.id,
    room: c.room,
    roomSid: c.roomSid,
    startedAt: c.startedAt,
    endedAt: c.endedAt,
    durationSeconds: c.durationSeconds,
    participantCount: c.participantCount,
    endState: c.endState,
    channel: channelOf(c),
    pipeline: model.pipeline,
    models: model.models,
    agentKey: agentKeyOf(c),
    turnCount: turnCount(c),
    answerMs: answers.length ? answers : null,
    answerMsMedian: median(answers),
    issues: {
      high: checked ? findings.filter((f) => f.severity === "high").length : null,
      medium: checked ? findings.filter((f) => f.severity === "medium").length : null,
      titles: findings.filter((f) => f.severity !== "info").map((f) => f.title),
    },
    tags: tagsOf(c),
    archived: isArchived(c),
    recorded: Boolean(c.recording && !c.recording.preview),
  }
}
