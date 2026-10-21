import fixtures from "@/mocks/calls.json"
import captureSession from "@/mocks/capture-session.json"
import captureCloud from "@/mocks/capture-cloud.json"
import captureCrash from "@/mocks/capture-crash.json"
import captureSip from "@/mocks/capture-sip.json"
import consoleSession from "@/mocks/console-session.json"
import consoleRealtime from "@/mocks/console-realtime.json"

import type { Clock } from "@/lib/api-types"
import { recordingId } from "./recordings"
import type { Call, Recording, SessionEvent, Span, Turn } from "@/lib/api"

// The captured calls the mock API serves, adapted to the contract. Only the mock layer reads these.

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
    // Captured recordings carry no media id; the file name (the room SID) stands in for one.
    recording: raw.recording && { ...raw.recording, id: raw.recording.id ?? recordingId(raw.recording.url), clockId: raw.recording.clockId ?? "recording" },
  }
}

// SDK custom fields and live state, which no capture carries yet. The values are the final state of
// the SDK conformance batch sdk/contract/examples/valid/full-call.json (its fields event, and each
// actor's last state event), shown on one call so the call page has something to draw; every other
// call has no SDK, so its fields are empty and its live state null.
const SDK_CALL = "spike-room-11"
const sdk: Pick<Call, "fields" | "live"> = {
  fields: { customer: "acme", plan: "trial" },
  live: { user: { state: "listening", at: "2026-10-21T02:21:52.200Z" }, agent: { state: "listening", at: "2026-10-21T02:21:55.810Z" } },
}
const withSdk = (c: Call): Call => (c.room === SDK_CALL ? { ...c, ...sdk } : { ...c, fields: {}, live: null })

const calls = [captureSip.call as unknown as RawCall, captureCrash.call as unknown as RawCall, captureCloud.call as unknown as RawCall, captureSession.call as unknown as RawCall, consoleRealtime.call as RawCall, consoleSession.call as RawCall, ...(fixtures.calls as RawCall[])].map(fromCapture).map(withSdk)

export const capturedCalls = (): Call[] => calls
