import { expect, it } from "vitest"
import { type Issue, lastSortKey } from "../src/features/issues/issues-model.ts"

const issue = (id: string, kind: "event" | "standing", at: (number | null)[]) => ({ id, kind, hits: at.map((t) => ({ at: t })) }) as unknown as Issue

// Sorting by Last, newest first: what is true now leads, an undated event trails.
it("sorts standing issues as now and undated events last", () => {
  const rows = [issue("old", "event", [100]), issue("current", "standing", []), issue("undated", "event", [null]), issue("new", "event", [100, 900])]
  const newestFirst = [...rows].sort((a, b) => lastSortKey(b) - lastSortKey(a)).map((i) => i.id)
  expect(newestFirst).toEqual(["current", "new", "old", "undated"])
})
