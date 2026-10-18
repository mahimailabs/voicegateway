import { infiniteQueryOptions } from "@tanstack/react-query"
import { currentScope } from "@/lib/url-state"
import type { AlertEvents, AlertSetup, CapacityRun, CreatedProjectKey, Invitations, Member, Members, ProjectKeys, IssueDispositions, ProjectSettings, Workspaces, Call as GeneratedCall, CallPage, CallStats, ProviderProbes, Workers, CloudAgents, CallSummary, CallTotals, Egress, EndOfTurnEvent, Ingress, IssueList, MediaServer, ProjectSnapshot, Sip, ItemEvent, OverlapEvent, RecordingSegment, Span, StateEvent, TranscriptEvent, Turn } from "@/lib/api-types"

export type { CallSummary, CallTotals, RecordingSegment, Span, Turn }
export type SessionEvent = StateEvent | TranscriptEvent | EndOfTurnEvent | OverlapEvent | ItemEvent
// preview is a console-only marker for the dev design preview, not part of the API contract.
export type Recording = NonNullable<GeneratedCall["recording"]> & { preview?: string }
export type Call = Omit<GeneratedCall, "recording"> & { recording?: Recording | null }

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

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { ...init, headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) } })
  } catch (e) {
    throw new ApiError(0, "network", e instanceof Error ? e.message : "No answer from the API")
  }
  if (res.status === 204) return undefined as T
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

// Only the totals and the first row, for counts beside the list (one page of one call).
const fetchFirstPage = (filters: URLSearchParams) => {
  const q = new URLSearchParams(filters)
  q.set("limit", "1")
  return getJSON<CallPage>(`${projectApi()}/calls?${q}`)
}
export const fetchCallCount = async (filters: URLSearchParams) => (await fetchFirstPage(filters)).totals.callCount

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
// The totals over every call matching the filters, such as how many are recorded.
export const callTotalsQuery = (filters: URLSearchParams) => ({ queryKey: ["calls", currentScope(), "totals", filters.toString()], queryFn: async () => (await fetchFirstPage(filters)).totals })
// The most recent call matching the filters, or null when none does.
export const latestMatchingCallQuery = (filters: URLSearchParams) => ({
  queryKey: ["calls", currentScope(), "latest", filters.toString()],
  queryFn: async () => {
    const q = new URLSearchParams(filters)
    q.set("sort", "startedAt")
    q.set("order", "desc")
    return (await fetchFirstPage(q)).items[0] ?? null
  },
})
// The call that ran in one LiveKit room, matched by the room's SID so a reused room name never opens
// another room's call. It pages through every call with that room name; null only when none matches,
// and an error when the search stops early, so a partial search is never reported as no call.
const MAX_ROOM_PAGES = 20
export const callInRoomQuery = (name: string, sid: string) => ({
  queryKey: ["calls", currentScope(), "in-room", sid],
  queryFn: async () => {
    const filters = new URLSearchParams({ room: name, sort: "startedAt", order: "desc" })
    let next: { cursor: string; snapshot: string } | undefined
    for (let page = 0; page < MAX_ROOM_PAGES; page++) {
      const p = await fetchCallPage(filters, next, 100)
      const hit = p.items.find((c) => c.roomSid === sid)
      if (hit) return hit.id
      if (!p.page.nextCursor) return null
      next = { cursor: p.page.nextCursor, snapshot: p.page.snapshot }
    }
    throw new ApiError(0, "search_cut_short", `More than ${MAX_ROOM_PAGES * 100} calls use room ${name}; the search stopped before finding this room's call.`)
  },
})
export const callQuery = (id: string) => ({ queryKey: ["call", currentScope(), id], queryFn: () => fetchCall(id) })

// The LiveKit pages' reads, one document each.
export const egressQuery = () => ({ queryKey: ["egress", currentScope()], queryFn: () => getJSON<Egress>(`${projectApi()}/egress`) })
export const ingressQuery = () => ({ queryKey: ["ingress", currentScope()], queryFn: () => getJSON<Ingress>(`${projectApi()}/ingress`) })
export const sipQuery = () => ({ queryKey: ["sip", currentScope()], queryFn: () => getJSON<Sip>(`${projectApi()}/sip`) })
export const mediaServerQuery = () => ({ queryKey: ["media-server", currentScope()], queryFn: () => getJSON<MediaServer>(`${projectApi()}/media-server`) })
export const cloudAgentsQuery = () => ({ queryKey: ["agents", currentScope()], queryFn: () => getJSON<CloudAgents>(`${projectApi()}/agents`) })
export const capacityQuery = () => ({ queryKey: ["capacity", currentScope()], queryFn: () => getJSON<CapacityRun | null>(`${projectApi()}/capacity`) })
export const workersQuery = () => ({ queryKey: ["workers", currentScope()], queryFn: () => getJSON<Workers>(`${projectApi()}/workers`) })
export const providerProbesQuery = () => ({ queryKey: ["provider-probes", currentScope()], queryFn: () => getJSON<ProviderProbes>(`${projectApi()}/provider-probes`) })
export const snapshotQuery = () => ({ queryKey: ["snapshot", currentScope()], queryFn: () => getJSON<ProjectSnapshot>(`${projectApi()}/snapshot`) })

// Issues and alert evaluation over all of time up to now: the pages have no range picker yet.
const allTime = () => new URLSearchParams({ from: new Date(0).toISOString(), to: new Date(Date.now() + 60_000).toISOString() })
export const issuesQuery = () => ({ queryKey: ["issues", currentScope()], queryFn: () => getJSON<IssueList>(`${projectApi()}/issues?${allTime()}`) })
export const alertEventsQuery = () => ({ queryKey: ["alert-events", currentScope()], queryFn: () => getJSON<AlertEvents>(`${projectApi()}/alert-events?${allTime()}`) })

// Overview's aggregates over a range, computed by the server over every call in it.
export const callStatsQuery = (from: Date, to: Date) => ({
  queryKey: ["call-stats", currentScope(), from.toISOString(), to.toISOString()],
  queryFn: () => getJSON<CallStats>(`${projectApi()}/call-stats?${new URLSearchParams({ from: from.toISOString(), to: to.toISOString() })}`),
})

// Settings. Until web stores them the console saves them in this browser (lib/local-store.ts) and
// these reads return them; after a save the console invalidates the read it changed.
export const projectSettingsQuery = () => ({ queryKey: ["settings", currentScope()], queryFn: () => getJSON<ProjectSettings>(`${projectApi()}/settings`) })
export const issueDispositionsQuery = () => ({ queryKey: ["issue-dispositions", currentScope()], queryFn: () => getJSON<IssueDispositions>(`${projectApi()}/issue-dispositions`) })
export const alertSetupQuery = () => ({ queryKey: ["alert-setup", currentScope()], queryFn: () => getJSON<AlertSetup>(`${projectApi()}/alert-setup`) })
export const invitationsQuery = () => ({ queryKey: ["invitations", currentScope().org], queryFn: () => getJSON<Invitations>(`/api/v1/org/${currentScope().org}/invitations`) })
export const fetchWorkspaces = () => getJSON<Workspaces>("/api/v1/workspaces")

// The organisation's members (phase 06b). Any member reads the list; an Admin changes roles and
// removes people, and the server refuses taking away the last admin with last_admin.
const membersApi = () => `/api/v1/org/${currentScope().org}/members`
export const membersQuery = () => ({ queryKey: ["members", currentScope().org], queryFn: () => getJSON<Members>(membersApi()) })
export const updateMember = (userId: string, role: Member["role"]) => getJSON<Member>(`${membersApi()}/${encodeURIComponent(userId)}`, { method: "PATCH", body: JSON.stringify({ role }) })
// A project's API keys (phase 06c), Admin only. The full key is in createProjectKey's answer and
// nowhere else.
const keysApi = (projectId: string) => `/api/v1/org/${currentScope().org}/project/${encodeURIComponent(projectId)}/keys`
export const projectKeysQuery = (projectId: string) => ({ queryKey: ["project-keys", currentScope().org, projectId], queryFn: () => getJSON<ProjectKeys>(keysApi(projectId)) })
export const createProjectKey = (projectId: string, name: string) => getJSON<CreatedProjectKey>(keysApi(projectId), { method: "POST", body: JSON.stringify({ name }) })
export const revokeProjectKey = (projectId: string, keyId: string) => getJSON<void>(`${keysApi(projectId)}/${encodeURIComponent(keyId)}`, { method: "DELETE" })
export const removeMember = (userId: string) => getJSON<void>(`${membersApi()}/${encodeURIComponent(userId)}`, { method: "DELETE" })
