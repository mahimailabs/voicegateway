import fixtures from "@/mocks/calls.json"
import captureSession from "@/mocks/capture-session.json"
import captureCloud from "@/mocks/capture-cloud.json"
import captureCrash from "@/mocks/capture-crash.json"
import captureSip from "@/mocks/capture-sip.json"
import consoleSession from "@/mocks/console-session.json"
import consoleRealtime from "@/mocks/console-realtime.json"

import type { Call as GeneratedCall, Clock, EndOfTurnEvent, ItemEvent, OverlapEvent, RecordingSegment, Span, StateEvent, TranscriptEvent, Turn } from "@/lib/api-types"

export type { RecordingSegment, Span, Turn }
export type SessionEvent = StateEvent | TranscriptEvent | EndOfTurnEvent | OverlapEvent | ItemEvent
// preview is a console-only marker for the dev design preview, not part of the API contract.
export type Recording = NonNullable<GeneratedCall["recording"]> & { preview?: string }
export type Call = Omit<GeneratedCall, "recording"> & { recording?: Recording | null }

// Capture fixtures predate the contract: they lack the call id, end state, tags, archive flag and clocks.
type Raw<T> = T extends unknown ? Omit<T, "clockId"> & { clockId?: string } : never
type RawCall = Omit<Call, "id" | "endState" | "tags" | "archived" | "clocks" | "spans" | "turns" | "events" | "recording"> &
  Partial<Pick<Call, "id" | "endState" | "tags" | "archived" | "clocks">> & {
    spans: Raw<Span>[] | null
    turns: Raw<Turn>[] | null
    events?: Raw<SessionEvent>[] | null
    recording?: (Omit<Raw<Recording>, "id"> & { id?: string }) | null
  }

const clock = (id: string, origin: Clock["origin"]): Clock => ({ id, origin, originAt: null, offsetToCallStartSeconds: null, alignment: "unknown" })

export function fromCapture(raw: RawCall): Call {
  const clocks: Clock[] = []
  if (raw.spans?.length) clocks.push(clock("trace", "trace_start"))
  if (raw.turns?.length || raw.events?.length) clocks.push(clock("session", "first_event"))
  if (raw.recording) clocks.push(clock("recording", "recording_start"))
  return {
    ...raw,
    // Preview fixtures have no conversation id yet; #323 replaces this.
    id: raw.id ?? raw.room,
    endState: raw.endState ?? "unknown",
    tags: raw.tags ?? [],
    archived: raw.archived ?? false,
    clocks: raw.clocks ?? clocks,
    spans: raw.spans?.map((s) => ({ ...s, clockId: s.clockId ?? "trace" })) ?? null,
    turns: raw.turns?.map((t) => ({ ...t, clockId: t.clockId ?? "session" })) ?? null,
    events: raw.events?.map((e) => ({ ...e, clockId: e.clockId ?? "session" })),
    // Captured recordings carry no media id; the playback url is the only identifier they have.
    recording: raw.recording && { ...raw.recording, id: raw.recording.id ?? raw.recording.url, clockId: raw.recording.clockId ?? "recording" },
  }
}

const calls = [captureSip.call as unknown as RawCall, captureCrash.call as unknown as RawCall, captureCloud.call as unknown as RawCall, captureSession.call as unknown as RawCall, consoleRealtime.call as RawCall, consoleSession.call as RawCall, ...(fixtures.calls as RawCall[])].map(fromCapture)

export const listCalls = (): Call[] => calls
export const getCall = (room: string): Call | undefined => calls.find((c) => c.room === room)
