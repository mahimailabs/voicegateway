import { expect, test } from "vitest"
import { freshness, observedState } from "../src/lib/live-status.ts"

const now = Date.parse("2026-10-20T12:00:00Z")
const status = (age = 0, errorClass: null | "unreachable" = null) => ({ lastReadAt: new Date(now).toISOString(), lastGoodReadAt: new Date(now - age).toISOString(), errorClass })
test("successful HTTP reads cannot freshen stale source samples", () => {
  expect(freshness(status(29999), 10000, now)).toBe("current")
  expect(freshness(status(30000), 10000, now)).toBe("stale")
  expect(freshness(status(0, "unreachable"), 10000, now)).toBe("unavailable")
  expect(freshness(undefined, 10000, now)).toBe("unknown")
  expect(freshness({ ...status(), lastGoodReadAt: "invalid" }, 10000, now)).toBe("unknown")
  expect(freshness(status(-60000), 10000, now)).toBe("unknown")
})
test("room or worker absence requires a newer successful snapshot, not a failed scrape", () => {
  expect(observedState(now, status(), 10000, now)).toBe("Observed active")
  expect(observedState(now, status(0, "unreachable"), 10000, now)).toBe("Last observed")
  expect(observedState(now, status(), 10000, now + 30000)).toBe("Last observed")
  expect(observedState(now, status(), 10000, now, true)).toBe("Last observed")
  expect(observedState(now - 10000, status(), 10000, now)).toBe("No longer listed")
  expect(observedState(null, status(), 10000, now)).toBe("Unknown")
})
