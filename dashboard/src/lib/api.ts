import { infiniteQueryOptions } from "@tanstack/react-query"
import { capturedCalls } from "@/mocks/fixtures"
import { currentScope } from "@/lib/url-state"
import type { AlertEvents, CapacityRun, Call as GeneratedCall, CallPage, CloudAgents, CallSummary, CallTotals, Egress, EndOfTurnEvent, Ingress, IssueList, MediaServer, ProjectSnapshot, Sip, ItemEvent, OverlapEvent, RecordingSegment, Span, StateEvent, TranscriptEvent, Turn } from "@/lib/api-types"

export type { CallSummary, CallTotals, RecordingSegment, Span, Turn }
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
  // Anything but JSON came from something other than the API, such as the dev server answering
  // with the app's own page when no API runs.
  const noApi = () => new ApiError(0, "no_api", `No VoiceGateway API answered at ${url.split("?")[0]}.`)
  if (!res.headers.get("Content-Type")?.includes("json")) throw noApi()
  const body: unknown = await res.json().catch(() => {
    throw noApi()
  })
  if (!res.ok) {
    const err = body as { code?: string; message?: string } | null
    if (!err?.code) throw noApi()
    throw new ApiError(res.status, err.code, err.message ?? res.statusText)
  }
  return body as T
}

// One page of the calls matching the filters, sorted and paged by the server. Later pages carry the
// cursor and the snapshot of the first, so they never mix two reads; totals cover every match.
export function fetchCallPage(filters: URLSearchParams, next?: { cursor: string; snapshot: string }, limit = 25): Promise<CallPage> {
  const q = new URLSearchParams(filters)
  q.set("limit", String(limit))
  if (next) {
    q.set("cursor", next.cursor)
    q.set("snapshot", next.snapshot)
  }
  return getJSON<CallPage>(`${projectApi()}/calls?${q}`)
}

// Only the totals and the first rows, for counts beside the list (one page of one call).
export const fetchCallCount = async (filters: URLSearchParams) => {
  const q = new URLSearchParams(filters)
  q.set("limit", "1")
  return (await getJSON<CallPage>(`${projectApi()}/calls?${q}`)).totals.callCount
}

// The most recent calls, newest first, for lists that show a recent slice (the command palette).
export const recentCallsQuery = () => ({ queryKey: ["calls", currentScope(), "recent"], queryFn: async () => (await fetchCallPage(new URLSearchParams({ sort: "startedAt", order: "desc" }), undefined, 100)).items })

// The project's most recent call, unfiltered: where the time range ends, and whether there are calls at all.
export const fetchLatestCall = async () => (await getJSON<CallPage>(`${projectApi()}/calls?limit=1&sort=startedAt&order=desc`)).items[0] ?? null

export const fetchCall = (id: string) => getJSON<Call>(`${projectApi()}/calls/${encodeURIComponent(id)}`)

// A 4xx or no API at all will not change by asking again; anything else is retried once.
export const shouldRetry = (failures: number, error: unknown) => !(error instanceof ApiError && (error.status === 0 || (error.status >= 400 && error.status < 500))) && failures < 1

// Query keys and functions for TanStack Query, scoped to the project so switching projects never
// shows another project's cache.
export const callPagesQuery = (filters: URLSearchParams) =>
  infiniteQueryOptions({
    queryKey: ["calls", currentScope(), "pages", filters.toString()],
    queryFn: ({ pageParam }) => fetchCallPage(filters, pageParam ?? undefined),
    initialPageParam: null as { cursor: string; snapshot: string } | null,
    getNextPageParam: (last: CallPage) => (last.page.nextCursor ? { cursor: last.page.nextCursor, snapshot: last.page.snapshot } : undefined),
  })
export const latestCallQuery = () => ({ queryKey: ["calls", currentScope(), "latest"], queryFn: fetchLatestCall })
export const callCountQuery = (filters: URLSearchParams) => ({ queryKey: ["calls", currentScope(), "count", filters.toString()], queryFn: () => fetchCallCount(filters) })
export const callQuery = (id: string) => ({ queryKey: ["call", currentScope(), id], queryFn: () => fetchCall(id) })

// The LiveKit pages' reads, one document each.
export const egressQuery = () => ({ queryKey: ["egress", currentScope()], queryFn: () => getJSON<Egress>(`${projectApi()}/egress`) })
export const ingressQuery = () => ({ queryKey: ["ingress", currentScope()], queryFn: () => getJSON<Ingress>(`${projectApi()}/ingress`) })
export const sipQuery = () => ({ queryKey: ["sip", currentScope()], queryFn: () => getJSON<Sip>(`${projectApi()}/sip`) })
export const mediaServerQuery = () => ({ queryKey: ["media-server", currentScope()], queryFn: () => getJSON<MediaServer>(`${projectApi()}/media-server`) })
export const cloudAgentsQuery = () => ({ queryKey: ["agents", currentScope()], queryFn: () => getJSON<CloudAgents>(`${projectApi()}/agents`) })
export const capacityQuery = () => ({ queryKey: ["capacity", currentScope()], queryFn: () => getJSON<CapacityRun | null>(`${projectApi()}/capacity`) })
export const snapshotQuery = () => ({ queryKey: ["snapshot", currentScope()], queryFn: () => getJSON<ProjectSnapshot>(`${projectApi()}/snapshot`) })

// Issues and alert evaluation over all of time up to now: the pages have no range picker yet.
const allTime = () => new URLSearchParams({ from: new Date(0).toISOString(), to: new Date(Date.now() + 60_000).toISOString() })
export const issuesQuery = () => ({ queryKey: ["issues", currentScope()], queryFn: () => getJSON<IssueList>(`${projectApi()}/issues?${allTime()}`) })
export const alertEventsQuery = () => ({ queryKey: ["alert-events", currentScope()], queryFn: () => getJSON<AlertEvents>(`${projectApi()}/alert-events?${allTime()}`) })
