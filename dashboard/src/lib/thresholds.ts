import { useSyncExternalStore } from "react"

// The numbers the checks judge against. They are settings, not measurements: one place sets them and
// every page reads them. Until the Phase 2 server stores them they live in this browser.

export type Thresholds = { answerMs: number; joinMs: number; ttsStartMs: number }
export const DEFAULTS: Thresholds = { answerMs: 1500, joinMs: 2000, ttsStartMs: 1000 }
const KEY = "vg.thresholds"

function validated(value: unknown): Thresholds {
  const saved = value && typeof value === "object" ? value as Partial<Thresholds> : {}
  const positive = (value: unknown, fallback: number) => typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback
  return {
    answerMs: positive(saved.answerMs, DEFAULTS.answerMs),
    joinMs: positive(saved.joinMs, DEFAULTS.joinMs),
    ttsStartMs: positive(saved.ttsStartMs, DEFAULTS.ttsStartMs),
  }
}

function load(): Thresholds {
  try {
    return validated(JSON.parse(localStorage.getItem(KEY) ?? "{}"))
  } catch {
    return DEFAULTS
  }
}

let current = load()
const listeners = new Set<() => void>()

// Live bindings: importers read the latest value on their next render.
export let BUDGET_MS = current.answerMs
export let JOIN_MS = current.joinMs
export let TTS_START_MS = current.ttsStartMs

export function setThresholds(next: Thresholds) {
  current = validated(next)
  BUDGET_MS = current.answerMs
  JOIN_MS = current.joinMs
  TTS_START_MS = current.ttsStartMs
  try {
    localStorage.setItem(KEY, JSON.stringify(current))
  } catch {
    // Storage is unavailable; the values last for this page view.
  }
  listeners.forEach((l) => l())
}

const subscribe = (l: () => void) => (listeners.add(l), () => listeners.delete(l))
export const useThresholds = () => useSyncExternalStore(subscribe, () => current, () => current)
