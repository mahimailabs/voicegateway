const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const ts = require("typescript")

const source = fs.readFileSync(path.join(__dirname, "../src/lib/thresholds.ts"), "utf8")
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
function load(saved, unavailable = false) {
  let stored = saved
  const exports = {}
  vm.runInNewContext(code, { exports, require, localStorage: {
    getItem() { if (unavailable) throw new Error("blocked"); return stored },
    setItem(key, value) { if (unavailable) throw new Error("blocked"); stored = value },
  } })
  return { api: exports, stored: () => JSON.parse(stored) }
}
function values(api) { return [api.BUDGET_MS, api.JOIN_MS, api.TTS_START_MS] }

test("valid saved thresholds survive reload", () => {
  assert.deepEqual(values(load('{"answerMs":1200,"joinMs":2500,"ttsStartMs":500}').api), [1200,2500,500])
})
test("invalid fields fall back independently without discarding valid values", () => {
  for (const invalid of ["invalid", "1500", null, 0, -1, {}, []]) {
    assert.deepEqual(values(load(JSON.stringify({ answerMs: invalid, joinMs: 3000, ttsStartMs: invalid })).api), [1500,3000,1000])
  }
  assert.deepEqual(values(load('{"answerMs":1e400}').api), [1500,2000,1000])
})
test("malformed or unavailable storage uses defaults", () => {
  for (const saved of [null, "{broken", "null", "42", "[]"]) assert.deepEqual(values(load(saved).api), [1500,2000,1000])
  assert.deepEqual(values(load(null, true).api), [1500,2000,1000])
})
test("updates validate and persist the same values read after reload", () => {
  const state = load(null)
  state.api.setThresholds({ answerMs: NaN, joinMs: 4000, ttsStartMs: Infinity })
  assert.deepEqual(values(state.api), [1500,4000,1000])
  assert.deepEqual(values(load(JSON.stringify(state.stored())).api), [1500,4000,1000])
})
test("updates remain usable when storage is blocked", () => {
  const state = load(null, true)
  state.api.setThresholds({ answerMs: 1000, joinMs: 1500, ttsStartMs: 700 })
  assert.deepEqual(values(state.api), [1000,1500,700])
})
