// @vitest-environment jsdom
import { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"
import { TooltipProvider } from "../src/components/ui/tooltip"
import { CallDiagnosis } from "../src/features/call/call-diagnosis"
import { buildModel } from "../src/features/call/call-model"
import type { Call } from "../src/lib/api"
import realtime from "../src/mocks/console-realtime.json"
import capture from "../src/mocks/capture-sip.json"
vi.mock("../src/lib/url-state", () => ({ useUrlState: (_key: string, initial: number) => useState(initial) }))
afterEach(cleanup)

test("opens the slowest turn, changes the evidence with selection, and keeps termination separate", () => {
  const call = capture.call as unknown as Call
  const model = buildModel(call)
  const openTranscript = vi.fn()
  render(<TooltipProvider><CallDiagnosis call={call} model={model} onConversation={openTranscript} /></TooltipProvider>)
  const slowest = [...model.turns].sort((a, b) => (b.responseMs ?? -1) - (a.responseMs ?? -1))[0]
  expect(screen.getByText(`Turn ${slowest.i + 1} · Response breakdown`)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: /^Turn 2 / }))
  expect(screen.getByText("Turn 2 · Response breakdown")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: /Open transcript/ }))
  expect(openTranscript).toHaveBeenCalledWith(1)
  expect(screen.getByText(call.endReason!)).toBeTruthy()
  expect(screen.getByText(/does not establish that response latency caused the disconnect/)).toBeTruthy()
})

test("missing turn timings do not create a fabricated breakdown", () => {
  const call = { ...capture.call, spans: [], turns: [], events: [], recording: null } as unknown as Call
  render(<TooltipProvider><CallDiagnosis call={call} model={buildModel(call)} onConversation={() => {}} /></TooltipProvider>)
  expect(screen.getByText("No turn timing evidence was captured for this call.")).toBeTruthy()
})


test("long calls keep six turn choices and advance to the next group", () => {
  const call = capture.call as unknown as Call
  const base = buildModel(call)
  const model = { ...base, turns: Array.from({ length: 120 }, (_, i) => ({ ...base.turns[0], i })) }
  render(<TooltipProvider><CallDiagnosis call={call} model={model} onConversation={() => {}} /></TooltipProvider>)
  expect(screen.getByText("Turns 1–6")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Next 6" }))
  expect(screen.getByText("Turns 7–12")).toBeTruthy()
  expect(screen.getByText("Turn 7 · Response breakdown")).toBeTruthy()
})

test("Realtime uses speech timing rather than fictitious STT and TTS stages", () => {
  const call = realtime.call as unknown as Call
  render(<TooltipProvider><CallDiagnosis call={call} model={buildModel(call)} onConversation={() => {}} /></TooltipProvider>)
  expect(screen.getByText("Realtime · audio-to-audio")).toBeTruthy()
  expect(screen.getByText("Speech-to-speech response gap")).toBeTruthy()
  expect(screen.queryByText("TTS TTFB")).toBeNull()
  expect(screen.getByText(/Not provider inference time/)).toBeTruthy()
})
