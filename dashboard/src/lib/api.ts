import { capturedCalls } from "@/mocks/fixtures"
import { currentScope } from "@/lib/url-state"
import type { Call as GeneratedCall, CallPage, CallSummary, EndOfTurnEvent, ItemEvent, OverlapEvent, RecordingSegment, Span, StateEvent, TranscriptEvent, Turn } from "@/lib/api-types"

export type { CallSummary, RecordingSegment, Span, Turn }
export type SessionEvent = StateEvent | TranscriptEvent | EndOfTurnEvent | OverlapEvent | ItemEvent
// preview is a console-only marker for the dev design preview, not part of the API contract.
export type Recording = NonNullable<GeneratedCall["recording"]> & { preview?: string }
export type Call = Omit<GeneratedCall, "recording"> & { recording?: Recording | null }

// Pages not yet moved to the API read the captured calls directly; #323 removes these as each page
// moves to fetch.
export const listCalls = (): Call[] => capturedCalls()
export const getCall = (room: string): Call | undefined => capturedCalls().find((c) => c.room === room)

// An API answer that is not 2xx. status 0 means no API answered.
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

// The current project's API root. The console reads only through web's API (CLAUDE.md Boundaries).
export const projectApi = () => `/api/v1/org/${currentScope().org}/project/${currentScope().project}`

async function getJSON<T>(url: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } })
  } catch (e) {
    throw new ApiError(0, "network", e instanceof Error ? e.message : "No answer from the API")
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { code?: string; message?: string } | null
    // An answer without the contract's error body came from something other than the API, such as
    // the dev server when no API runs.
    if (!body?.code) throw new ApiError(0, "no_api", `No VoiceGateway API answered at ${new URL(url, location.href).pathname}.`)
    throw new ApiError(res.status, body.code, body.message ?? res.statusText)
  }
  return (await res.json()) as T
}

// Every call summary in the project. The list is small enough to read whole for now; each page is
// requested with the snapshot of the first, so pages never mix two reads.
export async function fetchCallSummaries(): Promise<CallSummary[]> {
  const items: CallSummary[] = []
  let cursor: string | null = null
  let snapshot: string | null = null
  do {
    const q = new URLSearchParams({ limit: "100" })
    if (cursor && snapshot) {
      q.set("cursor", cursor)
      q.set("snapshot", snapshot)
    }
    const page: CallPage = await getJSON<CallPage>(`${projectApi()}/calls?${q}`)
    items.push(...page.items)
    cursor = page.page.nextCursor
    snapshot = page.page.snapshot
  } while (cursor)
  return items
}

export const fetchCall = (id: string) => getJSON<Call>(`${projectApi()}/calls/${encodeURIComponent(id)}`)

// Query keys and functions for TanStack Query, scoped to the project so switching projects never
// shows another project's cache.
export const callsQuery = () => ({ queryKey: ["calls", currentScope()], queryFn: fetchCallSummaries })
export const callQuery = (id: string) => ({ queryKey: ["call", currentScope(), id], queryFn: () => fetchCall(id) })
