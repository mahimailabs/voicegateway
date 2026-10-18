import { describe, expect, it } from "vitest"
import { fmtBytes, fmtSeconds } from "../src/lib/format.ts"

describe("format", () => {
  it.each([
    [4.24, "4.2 s"],
    [59.9, "59.9 s"],
    [65, "1m 05s"],
    [600, "10m 00s"],
  ])("fmtSeconds(%s) is %s", (s, want) => expect(fmtSeconds(s)).toBe(want))

  it.each([
    [12 * 1024, "12 kB"],
    [1.5 * 1024 ** 2, "1.5 MB"],
    [1024 ** 2, "1.0 MB"],
  ])("fmtBytes(%s) is %s", (b, want) => expect(fmtBytes(b)).toBe(want))
})
