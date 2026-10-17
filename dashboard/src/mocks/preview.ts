import { type Call, type CallSummary, fetchCall } from "@/lib/api"
import { fromCapture } from "./fixtures"
import { summarize } from "./summaries"

// Dev only: a design preview of the recording view, built from room 10's real trace plus synthetic speech
// generated locally into public/preview (gitignored). Never shipped, never presented as a captured call.
// The Calls page loads this module with import() in development only, so the captures stay out of a build.
export async function loadPreviewCall(): Promise<{ call: Call; summary: CallSummary } | null> {
  const rec = await fetch("/preview/recording.json").then((r) => (r.ok ? r.json() : null))
  if (!rec) return null
  const base = await fetchCall("spike-room-10")
  // Re-adapted without the base's id and clocks so the preview gets its own id and a recording clock.
  const call = fromCapture({
    ...base,
    id: undefined,
    clocks: undefined,
    room: "design-preview",
    roomSid: "PREVIEW",
    recording: { url: "/preview/recording.wav", duration: rec.duration, segments: rec.segments, peaks: rec.peaks, preview: "Design preview: room 10's real trace with synthetic speech made from its transcript. Not a captured call." },
  })
  return { call, summary: summarize(call) }
}
