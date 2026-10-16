import { useEffect, useMemo, useState } from "react"
import { type Call, type CallSummary, fetchCall } from "@/lib/api"
import { fromCapture } from "./fixtures"
import { summarize } from "./summaries"

// Dev only: a design preview of the recording view, built from room 10's real trace plus synthetic speech
// generated locally into public/preview (gitignored). Never shipped, never presented as a captured call.
export function usePreviewCall(): { call: Call; summary: CallSummary } | null {
  const [call, setCall] = useState<Call | null>(null)
  useEffect(() => {
    if (!import.meta.env.DEV) return
    fetch("/preview/recording.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((rec) => {
        if (!rec) return
        return fetchCall("spike-room-10").then((base) => {
        // Re-adapted without the base's id and clocks so the preview gets its own id and a recording clock.
        setCall(
          fromCapture({
            ...base,
            id: undefined,
            clocks: undefined,
            room: "design-preview",
            roomSid: "PREVIEW",
            recording: { url: "/preview/recording.wav", duration: rec.duration, segments: rec.segments, peaks: rec.peaks, preview: "Design preview: room 10's real trace with synthetic speech made from its transcript. Not a captured call." },
          }),
        )
        })
      })
      .catch(() => setCall(null))
  }, [])
  return useMemo(() => (call ? { call, summary: summarize(call) } : null), [call])
}
