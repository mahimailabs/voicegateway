import type { SourceStatus } from "@/lib/api-types"

export type Freshness = "current" | "stale" | "unavailable" | "unknown"
// Three source intervals, independent of whether the HTTP snapshot just succeeded.
export function freshness(status: SourceStatus | undefined, intervalMs: number, now: number): Freshness {
  if (status?.errorClass) return "unavailable"
  const last = status?.lastGoodReadAt ? Date.parse(status.lastGoodReadAt) : NaN
  if (!Number.isFinite(last) || last > now + intervalMs) return "unknown"
  return now - last >= intervalMs * 3 ? "stale" : "current"
}

export function observedState(lastSeen: number | null, status: SourceStatus | undefined, intervalMs: number, now: number, disconnected = false) {
  const lastGood = status?.lastGoodReadAt ? Date.parse(status.lastGoodReadAt) : NaN
  // Absence is meaningful only after a newer successful read, never after a failed read.
  if (lastSeen !== null && Number.isFinite(lastGood) && lastSeen < lastGood) return "No longer listed"
  if (disconnected || freshness(status, intervalMs, now) !== "current") return "Last observed"
  return lastSeen !== null && lastSeen === lastGood ? "Observed active" : "Unknown"
}
