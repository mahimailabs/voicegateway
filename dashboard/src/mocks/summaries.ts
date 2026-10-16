import type { Call, CallSummary } from "@/lib/api"
import { buildModel } from "@/features/call/call-model"
import { findingsFor } from "@/features/call/call-steps"
import type { MediaServerData } from "@/features/media-server/room-drawer"
import mediaServer from "@/mocks/media-server.json"

// What the server computes for the Calls list, here in the mock until web serves it. The checks
// are the call drawer's own (buildModel, findingsFor), so a row and its drawer never disagree.

const rooms = (mediaServer as unknown as MediaServerData).rooms

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}

// How a call reached the agent. Rooms we watched say it from their participants; console and local
// test sessions say it from where they were captured.
function channelOf(c: Call): CallSummary["channel"] {
  if (c.recording?.preview) return "local"
  const r = rooms.find((x) => x.sid === c.roomSid)
  if (r?.participants.some((p) => p.kind === "sip")) return "phone"
  if (r) return "web"
  if (c.room.startsWith("console")) return "console"
  if (c.room.startsWith("vg-sip")) return "phone"
  return c.room.startsWith("spike") ? "local" : "web"
}

// Agent turns from the spans when the call has them, else the reported turns; unknown when neither.
const turnCount = (c: Call) => (c.spans ? c.spans.filter((s) => s.name === "agent_turn").length : (c.turns?.length ?? null))

export function summarize(c: Call): CallSummary {
  const model = buildModel(c)
  const answers = model.turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
  const findings = findingsFor(c, model)
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
    models: model.models,
    turnCount: turnCount(c),
    answerMs: answers.length ? answers : null,
    answerMsMedian: median(answers),
    issues: {
      high: findings.filter((f) => f.severity === "high").length,
      medium: findings.filter((f) => f.severity === "medium").length,
      titles: findings.filter((f) => f.severity !== "info").map((f) => f.title),
    },
    tags: c.tags,
    archived: c.archived,
    recorded: Boolean(c.recording && !c.recording.preview),
  }
}
