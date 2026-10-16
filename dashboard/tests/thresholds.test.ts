import { afterEach, expect, test, vi } from "vitest"

// thresholds.ts reads storage when it loads, so each case loads a fresh copy over its own storage.
async function load(saved: string | null, unavailable = false) {
  let stored = saved
  vi.stubGlobal("localStorage", {
    getItem() {
      if (unavailable) throw new Error("blocked")
      return stored
    },
    setItem(_key: string, value: string) {
      if (unavailable) throw new Error("blocked")
      stored = value
    },
  })
  vi.resetModules()
  const api = await import("../src/lib/thresholds.ts")
  return { api, stored: () => JSON.parse(stored ?? "null") }
}
const values = (api: { BUDGET_MS: number; JOIN_MS: number; TTS_START_MS: number }) => [api.BUDGET_MS, api.JOIN_MS, api.TTS_START_MS]

afterEach(() => {
  vi.unstubAllGlobals()
})

test("valid saved thresholds survive reload", async () => {
  expect(values((await load('{"answerMs":1200,"joinMs":2500,"ttsStartMs":500}')).api)).toEqual([1200, 2500, 500])
})

test("invalid fields fall back independently without discarding valid values", async () => {
  for (const invalid of ["invalid", "1500", null, 0, -1, {}, []]) {
    expect(values((await load(JSON.stringify({ answerMs: invalid, joinMs: 3000, ttsStartMs: invalid }))).api)).toEqual([1500, 3000, 1000])
  }
  expect(values((await load('{"answerMs":1e400}')).api)).toEqual([1500, 2000, 1000])
})

test("malformed or unavailable storage uses defaults", async () => {
  for (const saved of [null, "{broken", "null", "42", "[]"]) expect(values((await load(saved)).api)).toEqual([1500, 2000, 1000])
  expect(values((await load(null, true)).api)).toEqual([1500, 2000, 1000])
})

test("updates validate and persist the same values read after reload", async () => {
  const state = await load(null)
  state.api.setThresholds({ answerMs: NaN, joinMs: 4000, ttsStartMs: Infinity })
  expect(values(state.api)).toEqual([1500, 4000, 1000])
  expect(values((await load(JSON.stringify(state.stored()))).api)).toEqual([1500, 4000, 1000])
})

test("updates remain usable when storage is blocked", async () => {
  const state = await load(null, true)
  state.api.setThresholds({ answerMs: 1000, joinMs: 1500, ttsStartMs: 700 })
  expect(values(state.api)).toEqual([1000, 1500, 700])
})
