import type { Call } from "@/lib/api"

// The user's archive and tags, which the server will store. Until it does, the console keeps them in
// this browser (call-archive.ts, call-tags.tsx); the mock runs in the same browser, so it reads the
// same storage and serves them in CallSummary like the server will. Unreadable or absent storage (a
// test in Node) means none.

function read<T>(key: string, empty: T): T {
  try {
    return (JSON.parse(localStorage.getItem(key) ?? "null") as T | null) ?? empty
  } catch {
    return empty
  }
}

export const isArchived = (c: Pick<Call, "roomSid" | "id">) => read<string[]>("vg.call-archive", []).includes(c.roomSid ?? c.id)
export const tagsOf = (c: Pick<Call, "room">) => read<Record<string, string[]>>("vg.call-tags", {})[c.room] ?? []
