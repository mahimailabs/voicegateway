import { expect, it } from "vitest"
import { type Issue, lastSortKey, standingState } from "../src/features/issues/issues-model.ts"

const issue = (id: string, kind: "event" | "standing", at: (number | null)[]) => ({ id, kind, hits: at.map((t) => ({ at: t,observedAt:t,evidenceState:kind === "standing" ? "current" : "recorded" })) }) as unknown as Issue

// Sorting by Last, newest first: what is true now leads, an undated event trails.
it("sorts standing issues as now and undated events last", () => {
  const rows = [issue("old", "event", [100]), issue("current", "standing", [null]), issue("undated", "event", [null]), issue("new", "event", [100, 900])]
  const newestFirst = [...rows].sort((a, b) => lastSortKey(b) - lastSortKey(a)).map((i) => i.id)
  expect(newestFirst).toEqual(["current", "new", "old", "undated"])
})

it("does not promote old component evidence to current when its source is stale or unavailable",()=>{
 const row=issue("old","standing",[100])
 row.hits[0].evidenceState="stale"
 expect(standingState(row)).toBe("stale")
 expect(lastSortKey(row)).toBe(100)
 row.hits[0].evidenceState="unavailable"
 expect(standingState(row)).toBe("unavailable")
 row.hits[0].evidenceState="unknown"
 expect(standingState(row)).toBe("unknown")
 expect(standingState(issue("missing","standing",[]))).toBe("unknown")
})
