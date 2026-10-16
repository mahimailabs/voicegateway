import fixtures from "@/mocks/calls.json"
import captureSession from "@/mocks/capture-session.json"
import captureCloud from "@/mocks/capture-cloud.json"
import captureCrash from "@/mocks/capture-crash.json"
import captureSip from "@/mocks/capture-sip.json"
import consoleSession from "@/mocks/console-session.json"
import consoleRealtime from "@/mocks/console-realtime.json"

// Round-1 types, written by hand until the OpenAPI contract exists (Phase 1 check 6 replaces them with generated ones).
export type Span = { name: string; spanId: string; parentId: string | null; startMs: number; durationMs: number; attrs?: Record<string, string> }
export type Turn = {
  userText: string | null
  agentText: string | null
  model: string | null
  llmMs: number | null
  ttftMs: number | null
  sttMs: number | null
  ttsMs: number | null
  endOfTurnMs: number | null
  inputTokens: number | null
  outputTokens: number | null
  audioSeconds: number | null
  characters: number | null
  // From LiveKit's per-message metrics (RemoteSession); absent on older captures.
  transcriptionDelayMs?: number | null
  e2eMs?: number | null
  eotProbability?: number | null
  eotThreshold?: number | null
  // Seconds from the session start when the user and agent messages were added.
  userItemAt?: number | null
  agentItemAt?: number | null
  // The agent's reply was cut off by the caller (the message's interrupted flag).
  interrupted?: boolean
}

// Agent session events (LiveKit RemoteSession protocol), seconds from the first event.
export type SessionEvent =
  | { t: number; type: "user_state"; from: string; to: string }
  | { t: number; type: "agent_state"; from: string; to: string }
  | { t: number; type: "transcript"; final: boolean; text: string }
  | { t: number; type: "end_of_turn"; probability: number; threshold: number }
  | { t: number; type: "overlapping_speech"; isInterruption: boolean; overlapStartedAt: number; detectedAt: number; detectionDelay: number }
  | { t: number; type: "item"; id: string; role: string }
export type Call = {
  room: string
  roomSid: string
  startedAt: string
  endedAt: string
  durationSeconds: number
  participantCount: number
  endReason: string | null
  spans: Span[] | null
  turns: Turn[] | null
  // Egress recording of the room when the project opts in (decision 0008). None of the captured calls have one.
  recording?: Recording | null
  events?: SessionEvent[] | null
  // Session totals when the source reported them.
  usage?: { llmInputTokens: number | null; llmOutputTokens: number | null; ttsCharacters: number | null; sttSeconds: number | null; note?: string } | null
}

export type RecordingSegment = { speaker: "caller" | "agent"; text: string; start: number; end: number }
export type Recording = {
  url: string
  duration: number
  segments: RecordingSegment[]
  // Normalised amplitude per bar, 0 to 1, per speaker track (Egress track recordings).
  peaks: { caller: number[]; agent: number[] }
  // Set when the audio is a design preview rather than a captured recording.
  preview?: string
}

const calls = [captureSip.call as unknown as Call, captureCrash.call as unknown as Call, captureCloud.call as unknown as Call, captureSession.call as unknown as Call, consoleRealtime.call as Call, consoleSession.call as Call, ...(fixtures.calls as Call[])]

export const listCalls = (): Call[] => calls
export const getCall = (room: string): Call | undefined => calls.find((c) => c.room === room)
