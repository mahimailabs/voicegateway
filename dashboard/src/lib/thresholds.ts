import type { Thresholds } from "@/lib/api-types"

// The numbers the checks judge against. They are settings, not measurements: getProjectSettings
// returns them and every page reads them from here. Saving goes to this browser until web stores it.

export type { Thresholds }
// The contract's defaults, for "Reset to defaults" and for a project whose settings could not be read.
export const DEFAULTS: Thresholds = { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }

// Live bindings: importers read the latest value on their next render.
export let BUDGET_MS = DEFAULTS.answerMs
export let JOIN_MS = DEFAULTS.joinMs
export let TTS_START_MS = DEFAULTS.ttsStartMs

// Puts the project's thresholds, as the API answered them, in place for the pages. The app shell does
// this before it renders a page and renders the page again when they change. An embed token cannot
// read settings, so embedded widgets judge against the contract's defaults until the contract gives
// them the thresholds.
export function applyThresholds(t: Thresholds) {
  BUDGET_MS = t.answerMs
  JOIN_MS = t.joinMs
  TTS_START_MS = t.ttsStartMs
}
