// What the console saves before the server can store it. Until web takes these writes they stay in
// this browser, and the mock API reads them back from here as the server will read its own tables.
// When storage is blocked a value lasts for this page view.

const memory = new Map<string, string>()
// Keys whose last write storage refused: memory holds the newer value for them.
const unsaved = new Set<string>()

// The saved JSON under key, or undefined when nothing is saved or it does not parse. The reader
// validates the shape, as the server validates what it stores.
export function readLocal(key: string): unknown {
  let raw: string | null
  try {
    raw = unsaved.has(key) ? (memory.get(key) ?? null) : localStorage.getItem(key)
  } catch {
    raw = memory.get(key) ?? null
  }
  if (raw === null) return undefined
  try {
    return JSON.parse(raw) as unknown
  } catch {
    return undefined
  }
}

export function writeLocal(key: string, value: unknown) {
  const raw = JSON.stringify(value)
  memory.set(key, raw)
  try {
    localStorage.setItem(key, raw)
    unsaved.delete(key)
  } catch {
    // Storage is unavailable or full; the value lasts for this page view.
    unsaved.add(key)
  }
}
