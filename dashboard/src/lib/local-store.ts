import { type QueryClient, useQueryClient } from "@tanstack/react-query"

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

// The last value saved under each key, with the answer it was based on. Until the read is answered
// again, a further save starts from this value, so two quick saves do not undo each other.
const pending = new Map<string, { data: unknown; value: unknown }>()

// A save for one read: writes the value here, then asks the read again so every page shows what the
// API now answers. A change is the whole value or a function of the current one; `from` turns the
// read's answer into the saved shape.
export function useLocalSave<T, D = unknown>(key: string, queryKey: readonly unknown[], from?: (data: D | undefined) => T) {
  return localSave(useQueryClient(), key, queryKey, from)
}
export function localSave<T, D = unknown>(client: QueryClient, key: string, queryKey: readonly unknown[], from?: (data: D | undefined) => T) {
  return (change: T | ((current: T) => T)) => {
    const data = client.getQueryData<D>(queryKey)
    let value: T
    if (typeof change === "function") {
      const last = pending.get(key)
      const current = last && last.data === data ? (last.value as T) : (from as (d: D | undefined) => T)(data)
      value = (change as (current: T) => T)(current)
    } else value = change
    pending.set(key, { data, value })
    writeLocal(key, value)
    return client.invalidateQueries({ queryKey })
  }
}
