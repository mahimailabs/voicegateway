// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react"
import { afterEach, expect, test } from "vitest"
import type { Call, Span } from "../src/lib/api"
import capture from "../src/mocks/capture-session.json"
import { buildModel } from "../src/features/call/call-model"
import { descendants, turnSpans } from "../src/features/call/call-trace"
import { stepsFor, findingsFor, evidenceCheck } from "../src/features/call/call-steps"
import { CallUsage } from "../src/features/call/call-usage"
import { waterfallRows } from "../src/features/profile/ttfa-waterfall"

const base = (): Call => structuredClone(capture.call) as Call
const span = (spanId: string, name: string, parentId: string | null, startMs: number, attrs: Record<string,string> = {}): Span => ({ spanId, name, parentId, startMs, durationMs: 500, clockId: "trace", attrs })
afterEach(cleanup)

test("speech belongs to its ancestor turn, even when exports are unordered or a turn never spoke", () => {
  const c = base(); c.events = null; c.recording = null; c.turns = c.turns!.slice(0, 2)
  c.spans = [span("speech2", "agent_speaking", "tts2", 2500), span("tts2", "tts_node", "turn2", 2200), span("turn2", "agent_turn", null, 2000), span("turn1", "agent_turn", null, 1000)]
  const m = buildModel(c)
  expect(m.turns[0].speaking).toBeNull()
  expect(m.turns[1].speaking).toEqual({ start: 2.5, end: 3 })
  expect(turnSpans(c, 1).children.map((s) => s.spanId)).toEqual(["tts2", "speech2"])
})

test("span identity binds the API turn and descendants are cycle safe and clock scoped", () => {
  const c = base(); c.turns![0].spanId = "second"
  const root = span("second", "agent_turn", "nested", 2000)
  c.spans = [span("first", "agent_turn", null, 1000), root, span("nested", "llm_node", "second", 2100), { ...span("foreign", "tts_node", "second", 2200), clockId: "other" }]
  expect(turnSpans(c, 0).root?.spanId).toBe("second")
  expect(descendants(c.spans, root).map((s) => s.spanId)).toEqual(["nested"])
})

test("a span-only call draws reported stage latency without probes, speech timing or duration substitutes", () => {
  const c = base(); c.events = null; c.recording = null; c.turns = c.turns!.slice(0, 1)
  c.spans = [span("turn", "agent_turn", null, 1000), span("llm", "llm_node", "turn", 1100, { "lk.response.ttft": "0.2" }), span("tts", "tts_node", "turn", 1400, { "lk.response.ttfb": "0" })]
  const t = buildModel(c).turns[0]
  expect(t.utterance).toBeNull()
  expect(waterfallRows(c, t)?.map((r) => [r.label, Math.round(r.start), Math.round(r.end)])).toEqual([["LLM first token", 100, 300], ["TTS first audio", 400, 400]])
  c.spans[1].attrs!["lk.response.ttft"] = "-1"
  delete c.spans[2].attrs!["lk.response.ttfb"]
  expect(waterfallRows(c, t)).toBeNull()
})

test("an error is linked to its turn without inventing preemptive cancellation", () => {
  const c = base(); c.events = null; c.recording = null; c.turns = c.turns!.slice(0, 1)
  c.spans = [span("req", "llm_request", "llm", 1100, { "gen_ai.response.finish_reasons": '["error"]' }), span("llm", "llm_node", "turn", 1050), span("turn", "agent_turn", null, 1000)]
  const model = buildModel(c)
  const findings = findingsFor(c, model)
  expect(findings.find((f) => f.id === "discard")?.turns).toEqual([0])
  expect(stepsFor(c, model, model.turns[0]).find((s) => s.kind === "llm")?.summary).toContain("does not identify why")
  expect(JSON.stringify(findings)).not.toContain("were started and thrown away")
})

test("missing end-of-turn detector values stay unknown", () => {
  const c = base(); c.events = null; c.recording = null; c.turns = c.turns!.slice(0, 1)
  c.spans = [span("user", "user_turn", null, 10), span("det", "eou_detection", "user", 20), span("turn", "agent_turn", null, 1000)]
  const m = buildModel(c)
  const eot = stepsFor(c, m, m.turns[0]).find((s) => s.kind === "eot")!
  expect(eot.output.find((v) => v.label === "Detector probability")?.value).toBeNull()
})

test("usage renders measured zero separately from missing totals and labels cached input", () => {
  const c = base(); c.usage = { llmInputTokens: 0, llmOutputTokens: null, llmCachedTokens: 0, ttsCharacters: 12, ttsSeconds: 1.25, sttSeconds: null, note: "One request did not report output tokens." }
  render(<CallUsage call={c} />)
  expect(screen.getAllByText("0 tokens")).toHaveLength(2)
  expect(screen.getAllByText("Unknown")).toHaveLength(2)
  expect(screen.getByText("Cached input (included above)")).toBeTruthy()
  expect(screen.getByText("1.25 seconds")).toBeTruthy()
})

test("checks require reported evidence instead of treating missing fields as healthy", () => {
  const a = span("u", "user_turn", null, 0)
  expect(evidenceCheck([a], "lk.transcript_confidence", (v) => Number(v) >= 0.8)).toBeNull()
  expect(evidenceCheck([], "lk.transcript_confidence", (v) => Number(v) >= 0.8)).toBeNull()
  a.attrs!["lk.transcript_confidence"] = "0.5"
  expect(evidenceCheck([a], "lk.transcript_confidence", (v) => Number(v) >= 0.8)).toBe(false)
})


test("span speech gaps are derived only on the same clock and reported latency wins", () => {
  const c = base(); c.events = null; c.recording = null; c.turns = c.turns!.slice(0, 1)
  c.turns[0].e2eMs = null
  c.spans = [span("user", "user_turn", null, 0), span("speech", "user_speaking", "user", 0), span("turn", "agent_turn", null, 600), span("reply", "agent_speaking", "turn", 800)]
  expect(buildModel(c).turns[0]).toMatchObject({ responseMs: 300, responseDerived: true })
  c.turns[0].e2eMs = 250
  expect(buildModel(c).turns[0]).toMatchObject({ responseMs: 250, responseDerived: false })
  c.turns[0].e2eMs = null
  c.spans[1].clockId = "foreign"
  expect(buildModel(c).turns[0]).toMatchObject({ responseMs: null, responseDerived: false })
})

test.each([
  ["error", true],
  ['["error"]', true],
  ['["stop","error"]', true],
  ["no_error", false],
  ['["no_error"]', false],
  ['{"error":true}', false],
  ['["error",1]', false],
  ["", false],
])("model error evidence uses exact typed finish reasons: %s", (reason, expected) => {
  const c = base()
  c.spans = [span("request", "llm_request", null, 1000, { "gen_ai.response.finish_reasons": reason })]
  expect(findingsFor(c, buildModel(c)).some((finding) => finding.id === "discard")).toBe(expected)
})
