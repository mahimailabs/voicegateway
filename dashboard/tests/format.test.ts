import { describe, expect, it } from "vitest"
import { fmtBytes, fmtDateTime, fmtSeconds } from "../src/lib/format.ts"

describe("format", () => {
  it.each([
    [4.24, "4.2 s"],
    [59.9, "59.9 s"],
    [65, "1m 05s"],
    [600, "10m 00s"],
    [59.96, "1m 00s"],
    [119.6, "2m 00s"],
    [3599.7, "60m 00s"],
    [60.46, "1m 00s"],
  ])("fmtSeconds(%s) is %s", (s, want) => expect(fmtSeconds(s)).toBe(want))

  // The unit and decimal separator follow the host locale, so the expected text is built the same way.
  const unit = (unit: string, n: number, digits: number) =>
    new Intl.NumberFormat(undefined, { style: "unit", unit, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n)
  it.each([
    [12 * 1024, unit("kilobyte", 12, 0)],
    [1.5 * 1024 ** 2, unit("megabyte", 1.5, 1)],
    [1024 ** 2, unit("megabyte", 1, 1)],
  ])("fmtBytes(%s) is %s", (b, want) => expect(fmtBytes(b)).toBe(want))

  it("reads an unreported time as unknown", () => expect(fmtDateTime(Number.NaN)).toBe("unknown"))
})
