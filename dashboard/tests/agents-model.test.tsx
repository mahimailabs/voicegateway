import { expect, test } from "vitest"
import { reportedTime } from "../src/features/agents/agents-model.ts"

// A time LiveKit did not report (a Cloud agent version's deployedAt, a worker's last scrape) reads
// as not reported, never as "never" or blank: unknown is not the same as did not happen.
test("a missing reported time says not reported", () => {
  expect(reportedTime(null)).toBe("not reported")
  expect(reportedTime("2026-10-18T11:00:00Z")).not.toBe("not reported")
  expect(reportedTime("2026-10-18T11:00:00Z")).not.toBe("")
})
