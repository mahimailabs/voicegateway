import { infiniteQueryOptions } from "@tanstack/react-query"
import { currentScope } from "@/lib/url-state"
import { client } from "@/lib/api-types/client.gen"
import {
  createEmbedToken as createEmbedTokenCall,
  updateAllowedOrigins as updateAllowedOriginsCall,
  createInvitation as createInvitationCall,
  createPasswordReset as createPasswordResetCall,
  createProject as createProjectCall,
  createProjectKey as createProjectKeyCall,
  getAlertSetup,
  getCallMetadata,
  getCallTags,
  updateCallMetadata,
  updateIssueMetadata,
  getCall,
  getCallStats,
  getCapacity,
  getCloudAgents,
  getEgress,
  getIngress,
  getInvitations,
  getMediaServer,
  getMembers,
  getMyOrganisationEntitlement,
  getProjectSettings,
  getProjectSnapshot,
  getProviderProbes,
  getSip,
  getWorkers,
  getResources,
  getWorkspaces,
  listAlertEvents,
  listCalls,
  listIssues,
  listProjectKeys,
  removeMember as removeMemberCall,
  revokeInvitation as revokeInvitationCall,
  revokeProjectKey as revokeProjectKeyCall,
  updateMember as updateMemberCall,
  updateProjectSettings as updateProjectSettingsCall,
} from "@/lib/api-types"
import type { Call as GeneratedCall, CreateEmbedToken, UpdateProjectSettings, CallPage, ProjectCreate, CallSummary, CallTotals, EndOfTurnEvent, ItemEvent, Member, OverlapEvent, RecordingSegment, Span, StateEvent, TranscriptEvent, Turn } from "@/lib/api-types"

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

// Never render cached telemetry after the API denies access.
export const accessDenied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status)

// The console reads only through web's API (CLAUDE.md Boundaries), with the SDK hey-api generates
// from docs/api/openapi.yaml: paths, parameters and answers are typed by the contract. Every call
// throws an ApiError on failure, so a query sees one error type.
client.setConfig({
  // A Request needs an absolute URL; the browser has the page's origin, Node tests have none.
  baseUrl: globalThis.location?.origin ?? "http://localhost",
  throwOnError: true,
})

// Anything but JSON came from something other than the API, such as the dev server answering with
// the app's own page when no API runs.
const noApi = (url: string) => new ApiError(0, "no_api", `No VoiceGateway API answered at ${new URL(url).pathname}.`)

client.interceptors.response.use((response, request) => {
  if (response.status !== 204 && !response.headers.get("Content-Type")?.includes("json")) throw noApi(request.url)
  return response
})

client.interceptors.error.use((error, response, request) => {
  if (error instanceof ApiError) return error
  if (!response) return new ApiError(0, "network", error instanceof Error ? error.message : "No answer from the API")
  const body = error as { code?: unknown; message?: unknown } | null
  // A 2xx whose body is not JSON, or an error without the contract's code, is not the API.
  if (response.ok || typeof body?.code !== "string") return noApi(request?.url ?? response.url)
  return new ApiError(response.status, body.code, typeof body.message === "string" ? body.message : response.statusText)
})

// The current project and its organisation, as the contract's path parameters.
const scope = () => ({ orgId: currentScope().org, projectId: currentScope().project })
// Every SDK call throws on failure (set on the client too), which also types its answer as present.
const thrown = { throwOnError: true } as const
const data = async <T>(answer: Promise<{ data: T }>) => (await answer).data

// One page of the calls matching the filters, sorted and paged by the server. Later pages carry the
// cursor and the snapshot of the first, so they never mix two reads; totals cover every match.
export function fetchCallPage(filters: URLSearchParams, next?: { cursor: string; snapshot: string }, limit = 25): Promise<CallPage> {
  const q = new URLSearchParams(filters)
  q.set("limit", String(limit))
  if (next) {
    q.set("cursor", next.cursor)
    q.set("snapshot", next.snapshot)
  }
  return callsWith(q)
}

// listCalls with the filters as the URL holds them: a filter repeats its key once per value, which
// the console builds with URLSearchParams (call-filters.ts), so the SDK sends that string as is.
const callsWith = (q: URLSearchParams) => data(listCalls({ ...thrown, path: scope(), query: {}, querySerializer: () => q.toString() }))

// Only the totals and the first row, for counts beside the list (one page of one call).
const fetchFirstPage = (filters: URLSearchParams) => {
  const q = new URLSearchParams(filters)
  q.set("limit", "1")
  return callsWith(q)
}
export const fetchCallCount = async (filters: URLSearchParams) => (await fetchFirstPage(filters)).totals.callCount

// The most recent calls, newest first, for lists that show a recent slice (the command palette).
export const recentCallsQuery = () => ({ queryKey: ["calls", currentScope(), "recent"], queryFn: async () => (await fetchCallPage(new URLSearchParams({ sort: "startedAt", order: "desc" }), undefined, 100)).items })

// The project's most recent call, unfiltered: where the time range ends, and whether there are calls at all.
export const fetchLatestCall = async () => (await data(listCalls({ ...thrown, path: scope(), query: { limit: 1, sort: "startedAt", order: "desc" } }))).items[0] ?? null

export const fetchCall = (id: string): Promise<Call> => data(getCall({ ...thrown, path: { ...scope(), callId: id } }))

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
export const egressQuery = () => ({ queryKey: ["egress", currentScope()], queryFn: () => data(getEgress({ ...thrown, path: scope() })) })
export const ingressQuery = () => ({ queryKey: ["ingress", currentScope()], queryFn: () => data(getIngress({ ...thrown, path: scope() })) })
export const sipQuery = () => ({ queryKey: ["sip", currentScope()], queryFn: () => data(getSip({ ...thrown, path: scope() })) })
export function mediaServerQuery(selectedScope = currentScope()) {
  // Capture the scope with the key; retries must never read a newly selected project.
  const selected = { ...selectedScope }
  return {
    queryKey: ["media-server", selected],
    queryFn: ({ signal }: { signal: AbortSignal }) => data(getMediaServer({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })),
    staleTime: 0,
    refetchInterval: 10000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
  }
}
export function cloudAgentsQuery() {
  // Capture the scope with the key; retries must never read a newly selected project.
  const selected = currentScope()
  return {
    queryKey: ["agents", selected],
    queryFn: ({ signal }: { signal: AbortSignal }) => data(getCloudAgents({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })),
    staleTime: 0,
    refetchInterval: 60000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
  }
}
// The contract requires the agent whose run to read, and the console does not pass one yet: it
// shows one run for every agent. The cast keeps that known gap in one visible place.
export const capacityQuery = () => ({ queryKey: ["capacity", currentScope()], queryFn: () => data(getCapacity({ ...thrown, path: scope() } as Parameters<typeof getCapacity<true>>[0])) })
export function workersQuery() {
  // Capture the scope with the key; retries must never read a newly selected project.
  const selected = currentScope()
  return {
    queryKey: ["workers", selected],
    queryFn: ({ signal }: { signal: AbortSignal }) => data(getWorkers({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })),
    staleTime: 0,
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
  }
}
export function resourcesQuery() {
  // Capture the scope with the key; retries must never read a newly selected project.
  const selected = currentScope()
  return {
    queryKey: ["resources", selected],
    queryFn: ({ signal }: { signal: AbortSignal }) => data(getResources({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]) })),
    staleTime: 0,
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: false,
  }
}
export const providerProbesQuery = () => ({ queryKey: ["provider-probes", currentScope()], queryFn: () => data(getProviderProbes({ ...thrown, path: scope() })) })
export const snapshotQuery = () => ({ queryKey: ["snapshot", currentScope()], queryFn: () => data(getProjectSnapshot({ ...thrown, path: scope() })) })

// Issues and alert evaluation over all of time up to now: the pages have no range picker yet.
const allTime = () => ({ from: new Date(0).toISOString(), to: new Date(Date.now() + 60_000).toISOString() })
export const issuesQuery = () => ({ queryKey: ["issues", currentScope()], queryFn: () => data(listIssues({ ...thrown, path: scope(), query: allTime() })) })
// Issues over a range, the same groups getCallStats counts in issueCounts for that range.
export const issuesInQuery = (from: Date, to: Date) => ({
  queryKey: ["issues", currentScope(), from.toISOString(), to.toISOString()],
  queryFn: () => data(listIssues({ ...thrown, path: scope(), query: { from: from.toISOString(), to: to.toISOString() } })),
})
export const alertEventsQuery = () => ({ queryKey: ["alert-events", currentScope()], queryFn: () => data(listAlertEvents({ ...thrown, path: scope(), query: allTime() })) })

// Overview's aggregates over a range, computed by the server over every call in it.
export const callStatsQuery = (from: Date, to: Date) => ({
  queryKey: ["call-stats", currentScope(), from.toISOString(), to.toISOString()],
  queryFn: () => data(getCallStats({ ...thrown, path: scope(), query: { from: from.toISOString(), to: to.toISOString() } })),
})

// Bind each request to its query key, even if navigation happens before it executes.
export const projectSettingsQuery = () => {
  const selected = currentScope()
  return {
    queryKey: ["settings", selected],
    queryFn: ({ signal }: { signal: AbortSignal }) => data(getProjectSettings({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, signal })),
    staleTime: 15_000,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  }
}
export const saveProjectSettings = (selected: ReturnType<typeof currentScope>, body: UpdateProjectSettings) =>
  data(updateProjectSettingsCall({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, body }))
// Allowed embed origins (Admin) and test embed tokens, decision 0034.
export const saveAllowedOrigins = (selected: ReturnType<typeof currentScope>, allowedOrigins: string[], allowedOriginsRevision: number) =>
  data(updateAllowedOriginsCall({ ...thrown, path: { orgId: selected.org, projectId: selected.project }, body: { allowedOrigins, allowedOriginsRevision } }))
export const createEmbedToken = (body: CreateEmbedToken) => data(createEmbedTokenCall({ ...thrown, path: scope(), body }))
export const alertSetupQuery = () => ({ queryKey: ["alert-setup", currentScope()], queryFn: () => data(getAlertSetup({ ...thrown, path: scope() })) })
export const fetchWorkspaces = () => data(getWorkspaces(thrown))

// The organisation's members (phase 06b). Any member reads the list; an Admin changes roles and
// removes people, and the server refuses taking away the last admin with last_admin.
export const membersQuery = () => ({ queryKey: ["members", currentScope().org], queryFn: () => data(getMembers({ ...thrown, path: { orgId: currentScope().org } })) })
export const updateMember = (userId: string, role: Member["role"]) => data(updateMemberCall({ ...thrown, path: { orgId: currentScope().org, userId }, body: { role } }))
// A project's API keys (phase 06c), Admin only. The full key is in createProjectKey's answer and
// nowhere else.
const keysPath = (projectId: string) => ({ orgId: currentScope().org, projectId })
export const projectKeysQuery = (projectId: string) => ({ queryKey: ["project-keys", currentScope().org, projectId], queryFn: () => data(listProjectKeys({ ...thrown, path: keysPath(projectId) })) })
export const createProjectKey = (projectId: string, name: string) => data(createProjectKeyCall({ ...thrown, path: keysPath(projectId), body: { name } }))
export const revokeProjectKey = (projectId: string, keyId: string) => data(revokeProjectKeyCall({ ...thrown, path: { ...keysPath(projectId), keyId } })).then(() => undefined)
export const removeMember = (userId: string) => data(removeMemberCall({ ...thrown, path: { orgId: currentScope().org, userId } })).then(() => undefined)
// Invitations and password reset links (phase 06d), Admin only. With SMTP set the server emails a
// link and never answers it; without SMTP the link is in the create's answer and nowhere else.
export const invitationsQuery = () => ({ queryKey: ["invitations", currentScope().org], queryFn: () => data(getInvitations({ ...thrown, path: { orgId: currentScope().org } })) })
export const createInvitation = (email: string, role: Member["role"]) => data(createInvitationCall({ ...thrown, path: { orgId: currentScope().org }, body: { email, role } }))
export const revokeInvitation = (invitationId: string) => data(revokeInvitationCall({ ...thrown, path: { orgId: currentScope().org, invitationId } })).then(() => undefined)
export const createPasswordReset = (userId: string) => data(createPasswordResetCall({ ...thrown, path: { orgId: currentScope().org, userId } }))
// Adds a LiveKit project to an organisation (phase 06f), Admin only. The secret goes to web, which
// seals it; the answer is the new project's id and name, never a credential.
export const createProject = (orgId: string, body: ProjectCreate) => data(createProjectCall({ ...thrown, path: { orgId }, body }))

export const entitlementQuery = () => ({ queryKey: ["entitlement", currentScope().org], queryFn: () => data(getMyOrganisationEntitlement({ ...thrown, path: { orgId: currentScope().org } })), refetchInterval: 30_000, retry: false as const })

export const metadataQuery = (id: string, selected = currentScope()) => ({
 queryKey: ["call-metadata", selected, id],
 queryFn: ({signal}: {signal: AbortSignal}) => data(getCallMetadata({...thrown,path:{orgId:selected.org,projectId:selected.project,callId:id},signal})),
 staleTime: 0, retry: false,
})
export const callTagsQuery = (selected = currentScope()) => ({
 queryKey:["call-tags",selected],
 queryFn: ({signal}: {signal:AbortSignal}) => data(getCallTags({...thrown,path:{orgId:selected.org,projectId:selected.project},signal})),
 staleTime:0, retry:false,
})
export const saveCallMetadata = (id: string, body: import("@/lib/api-types").CallMetadata, selected = currentScope()) => data(updateCallMetadata({...thrown,path:{orgId:selected.org,projectId:selected.project,callId:id},body}))

export const saveIssueMetadata = (id: string, body: import("@/lib/api-types").UpdateIssueMetadata, selected = currentScope()) => data(updateIssueMetadata({...thrown,path:{orgId:selected.org,projectId:selected.project,issueId:id},body}))
