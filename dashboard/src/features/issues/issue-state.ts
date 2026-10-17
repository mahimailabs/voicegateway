import type { IssueDispositions } from "@/lib/api-types"
import { issueDispositionsQuery } from "@/lib/api"
import { useLocalSave } from "@/lib/local-store"

// What a person decided about an issue: resolved or ignored, and when. getIssueDispositions reads the
// decisions; saving goes to this browser until the Phase 2 server stores it. An issue resolved before
// it was last seen comes back as regressed.

export type IssueState = { state: "resolved" | "ignored"; at: number }
export type Store = Record<string, IssueState>

export const storeFrom = (d: IssueDispositions): Store => Object.fromEntries(d.items.map((x) => [x.issueId, { state: x.state, at: Date.parse(x.at) }]))
export const useSaveIssueStates = () => useLocalSave<Store, IssueDispositions>("vg.issue-state", issueDispositionsQuery().queryKey, (d) => (d ? storeFrom(d) : {}))

// Open unless resolved or ignored; resolved turns back to open (regressed) when seen again after.
export function statusOf(s: Store, id: string, lastSeen: number | null): { status: "open" | "resolved" | "ignored"; regressed: boolean; at?: number } {
  const x = s[id]
  if (!x) return { status: "open", regressed: false }
  if (x.state === "resolved" && lastSeen !== null && lastSeen * 1000 > x.at) return { status: "open", regressed: true, at: x.at }
  return { status: x.state, regressed: false, at: x.at }
}
