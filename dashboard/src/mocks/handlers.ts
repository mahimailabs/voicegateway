import { issueMetadataOf, saveIssueMetadata } from "./issue-metadata"
import { metadataOf, saveMetadata } from "./dispositions"
import { http, HttpResponse, type PathParams } from "msw"
import type { AlertEvents, AlertSetup, Call, CallPage, CallStats, CreatedInvitation, CreatedPasswordReset, CreatedProjectKey, Invitations, ProjectCreate, WorkspaceProject, Member, Members, ProjectConnection, ProjectKeys, IssueList, ProjectSettings, ProviderProbes, Resources, Workers, CapacityRun, CloudAgents, Egress, Error as ApiErrorBody, Ingress, MediaServer, ProjectSnapshot, Sip, Workspaces } from "@/lib/api-types"
import { callStats } from "./call-stats"
import { capturedCalls } from "./fixtures"
import { alertEvents, issueList } from "./insights"
import { capacity, cloudAgents, egress, ingress, mediaServer, projectSnapshot, providerProbes, sip, workers } from "./livekit"
import { summarize } from "./summaries"
import { matchesFilters, parseFilters, sortCalls, totals } from "./calls-query"
import { playbackUrl, recordingMedia } from "./recordings"
import { alertSetup, changeMember, createKey, invitations, invite, members, passwordReset, projectKeys, projectSettings, updateSettings, revokeInvitation, revokeKey } from "./settings"
import { addProject, knownOrg, knownProject, workspaces } from "./workspaces"

// Everything but the paging parameters, in a stable order, so a cursor can name what it was issued for.
function filterKey(q: URLSearchParams) {
  const kept = [...q.entries()].filter(([k]) => !["limit", "cursor", "snapshot"].includes(k)).sort(([a, x], [b, y]) => a.localeCompare(b) || x.localeCompare(y))
  let h = 0
  for (const ch of JSON.stringify(kept)) h = (h * 31 + ch.charCodeAt(0)) | 0
  return (h >>> 0).toString(36)
}

// The mock API: the contract's paths (docs/api/openapi.yaml) answered from the captured fixtures, so
// the console fetches exactly as it will from web. Only the paths a page has moved to are here.

// Any origin: the console calls its own origin, tests call a test host.
const project = "*/api/v1/org/:orgId/project/:projectId"

const fail = (status: number, code: ApiErrorBody["code"], message: string) =>
  HttpResponse.json<ApiErrorBody>({ code, message, requestId: `mock-${crypto.randomUUID()}` }, { status })

const settingsScope = (params: PathParams) => `${params.orgId}/${params.projectId}`
const known = (params: PathParams) => knownProject(params.orgId, params.projectId)
// A browser check's replacement fields for the connection read; none in Node or without storage.
function connectionOverride(): Partial<ProjectConnection> {
  try {
    return JSON.parse(globalThis.localStorage?.getItem("vg.mock-connection") ?? "{}") as Partial<ProjectConnection>
  } catch {
    return {}
  }
}

// Playback goes through the API in the project that was asked for, never to the captured file.
function withPlayback<T extends { recording?: { id: string; url: string } | null }>(x: T, params: PathParams): T {
  return x.recording ? { ...x, recording: { ...x.recording, url: playbackUrl(String(params.orgId), String(params.projectId), x.recording.id) } } : x
}
// Every recording a capture names, so an id alone never reaches a file.
const recordingIds = () => new Set([...capturedCalls(), ...mediaServer().rooms].flatMap((x) => (x.recording && !("preview" in x.recording && x.recording.preview) ? [x.recording.id] : [])))

// A required from/to range: inclusive start, exclusive end, both RFC 3339.
function range(q: URLSearchParams): { from: Date; to: Date } | string {
  const from = q.get("from")
  const to = q.get("to")
  if (from === null || to === null) return "from and to are required."
  const f = new Date(from)
  const t = new Date(to)
  if (Number.isNaN(+f) || Number.isNaN(+t)) return "from and to must be date-times."
  if (+t <= +f) return "to must be after from."
  return { from: f, to: t }
}

// The fixtures never change while the page is open, so one snapshot names them all.
const SNAPSHOT = "captured"

export const handlers = [
 http.get(`${project}/issues/:issueId/metadata`,({params})=>{
  if(!known(params)) return fail(404,"not_found","No such project.")
  const selected=settingsScope(params),id=String(params.issueId)
  if(!issueList(new Date(0),new Date(Date.now()+60000),selected).items.some(i=>i.id===id)) return fail(404,"not_found","No such issue.")
  return HttpResponse.json(issueMetadataOf(selected,id))
 }),
 http.put(`${project}/issues/:issueId/metadata`,async({params,request})=>{
  if(!known(params)) return fail(404,"not_found","No such project.")
  const selected=settingsScope(params), id=String(params.issueId)
  const exists=issueList(new Date(0),new Date(Date.now()+60000),selected).items.some(i=>i.id===id)
  if(!exists) return fail(404,"not_found","No such issue.")
  const body=await request.json() as import("@/lib/api-types").UpdateIssueMetadata
  const before=issueMetadataOf(selected,id)
  if(!Array.isArray(body.tags)||typeof body.archived!=="boolean"||!Number.isSafeInteger(body.revision)||!Number.isSafeInteger(body.evidenceRevision)|| (body.state!=null&&!['open','resolved','ignored'].includes(body.state))) return fail(400,"invalid_request","Invalid issue metadata.")
  if(body.revision!==before.revision||body.evidenceRevision!==before.evidenceRevision) return fail(409,"revision_conflict","Issue changed; reload before saving.")
  const next={...before,tags:[...new Set(body.tags.map(t=>t.trim().toLowerCase()))],archived:body.archived,revision:before.revision+1,...(body.state==null?{}:{state:body.state,regressed:false,decidedAt:new Date().toISOString(),reviewedEvidenceRevision:before.evidenceRevision})}
  saveIssueMetadata(selected,id,next)
  return HttpResponse.json(next)
 }),

 http.get(`${project}/call-tags`,({params})=>{
  if(!known(params)) return fail(404,"not_found","No such project.")
  return HttpResponse.json({tags:[...new Set(capturedCalls().flatMap(c=>metadataOf(settingsScope(params),c.id).tags))].sort()})
 }),
 http.get(`${project}/calls/:callId/metadata`,({params})=>{
  if(!known(params)||!capturedCalls().some(c=>c.id===params.callId)) return fail(404,"not_found","No such call.")
  return HttpResponse.json(metadataOf(settingsScope(params),String(params.callId)))
 }),
 http.put(`${project}/calls/:callId/metadata`,async({params,request})=>{
  if(!known(params)||!capturedCalls().some(c=>c.id===params.callId)) return fail(404,"not_found","No such call.")
  const body=await request.json() as {tags?:unknown;archived?:unknown;revision?:unknown}
  if(!Array.isArray(body.tags)||body.tags.length>32||body.tags.some(t=>typeof t!=="string"||!t.trim()||Array.from(t.trim()).length>64||/\p{Cc}/u.test(t))||typeof body.archived!=="boolean"||!Number.isSafeInteger(body.revision)) return fail(400,"invalid_request","Invalid metadata.")
  const before=metadataOf(settingsScope(params),String(params.callId))
  if(body.revision!==before.revision) return fail(409,"revision_conflict","Call metadata changed; reload before saving.")
  const next={tags:[...new Set((body.tags as string[]).map(t=>t.trim().toLowerCase()))].sort(),archived:body.archived,revision:before.revision+1}
  saveMetadata(settingsScope(params),String(params.callId),next)
  return HttpResponse.json(next)
 }),
  http.get<PathParams, never, CallPage | ApiErrorBody>(`${project}/calls`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const q = new URL(request.url).searchParams
    const limit = Number(q.get("limit") ?? 25)
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) return fail(400, "invalid_request", "limit must be 1 to 100.")
    const filters = parseFilters(q)
    if (typeof filters === "string") return fail(400, "invalid_request", filters)

    // A cursor is bound to the filters, sort and order it was issued for.
    const bound = filterKey(q)
    const cursor = q.get("cursor")
    let start = 0
    if (cursor !== null) {
      if (q.get("snapshot") !== SNAPSHOT) return fail(400, "invalid_request", "cursor needs the snapshot it came with.")
      const [offset, key] = cursor.split(":")
      start = Number(offset)
      if (!Number.isInteger(start) || start < 0 || key !== bound) return fail(400, "invalid_request", "The cursor does not belong to these filters.")
    }

    const matching = sortCalls(capturedCalls().map(c=>summarize({...c,...metadataOf(settingsScope(params),c.id)})).filter((c) => matchesFilters(c, filters)), filters)
    const items = matching.slice(start, start + limit)
    const next = start + limit < matching.length ? `${start + limit}:${bound}` : null
    return HttpResponse.json<CallPage>({ items, page: { nextCursor: next, snapshot: SNAPSHOT }, totals: totals(matching, projectSettings(settingsScope(params)).thresholds.answerMs) })
  }),

  http.get<PathParams, never, Call | ApiErrorBody>(`${project}/calls/:callId`, ({ params }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const call = capturedCalls().find((c) => c.id === params.callId)
    return call ? HttpResponse.json(withPlayback({...call,tags:metadataOf(settingsScope(params),call.id).tags,archived:metadataOf(settingsScope(params),call.id).archived}, params)) : fail(404, "not_found", "No such call in this project.")
  }),

  // Overview's aggregates, issues and alert evaluation over a range; both bounds are required.
  http.get<PathParams, never, CallStats | ApiErrorBody>(`${project}/call-stats`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const r = range(new URL(request.url).searchParams)
    return typeof r === "string" ? fail(400, "invalid_request", r) : HttpResponse.json(callStats(r.from, r.to, projectSettings(settingsScope(params)).thresholds.answerMs))
  }),
  http.get<PathParams, never, IssueList | ApiErrorBody>(`${project}/issues`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const r = range(new URL(request.url).searchParams)
    return typeof r === "string" ? fail(400, "invalid_request", r) : HttpResponse.json(issueList(r.from, r.to, settingsScope(params)))
  }),
  http.get<PathParams, never, AlertEvents | ApiErrorBody>(`${project}/alert-events`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const r = range(new URL(request.url).searchParams)
    return typeof r === "string" ? fail(400, "invalid_request", r) : HttpResponse.json(alertEvents(r.from, r.to))
  }),

  // LiveKit reads: one document each, as the capture recorded it.
  http.get<PathParams, never, Egress | ApiErrorBody>(`${project}/egress`, ({ params }) => (known(params) ? HttpResponse.json(egress()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, Ingress | ApiErrorBody>(`${project}/ingress`, ({ params }) => (known(params) ? HttpResponse.json(ingress()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, MediaServer | ApiErrorBody>(`${project}/media-server`, ({ params }) => (known(params) ? HttpResponse.json({ ...mediaServer(), rooms: mediaServer().rooms.map((r) => withPlayback(r, params)) }) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, CloudAgents | ApiErrorBody>(`${project}/agents`, ({ params }) => (known(params) ? HttpResponse.json(cloudAgents()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, CapacityRun | null | ApiErrorBody>(`${project}/capacity`, ({ params }) => (known(params) ? HttpResponse.json(capacity()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, ProjectSnapshot | ApiErrorBody>(`${project}/snapshot`, ({ params }) => (known(params) ? HttpResponse.json(projectSnapshot()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, Resources | ApiErrorBody>(`${project}/resources`, ({ params }) => (known(params) ? HttpResponse.json({source: "No container resource exporter configured.", resources: []}) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, Workers | ApiErrorBody>(`${project}/workers`, ({ params }) => (known(params) ? HttpResponse.json(workers()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, ProviderProbes | ApiErrorBody>(`${project}/provider-probes`, ({ params }) => (known(params) ? HttpResponse.json(providerProbes()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, Sip | ApiErrorBody>(`${project}/sip`, ({ params }) => (known(params) ? HttpResponse.json(sip()) : fail(404, "not_found", "No such project."))),

  // Settings, saved in this browser until web stores them (settings.ts). Writes stay in the console.
  http.get<PathParams, never, ProjectSettings | ApiErrorBody>(`${project}/settings`, ({ params }) => (known(params) ? HttpResponse.json(projectSettings(settingsScope(params))) : fail(404, "not_found", "No such project."))),
  http.put(`${project}/settings`, async ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    let body: unknown
    try { body = await request.json() } catch { return fail(400, "invalid_request", "Expected JSON settings.") }
    const saved = updateSettings(settingsScope(params), body)
    return typeof saved === "string" ? fail(saved === "revision_conflict" ? 409 : 400, saved, saved === "revision_conflict" ? "Settings changed; reload before saving." : "Invalid thresholds or revision.") : HttpResponse.json(saved)
  }),
  // First-call setup. The mock receives no ingest, so every source is still waiting and no first
  // call exists: a mock or a fixture never counts as the project's first stored call. The URLs use
  // this page's origin, as web builds them from VOICEGATEWAY_PUBLIC_URL. A browser check can replace
  // any top-level field through localStorage "vg.mock-connection" (a refusal, null URLs).
  http.get<PathParams, never, ProjectConnection | ApiErrorBody>(`${project}/connection`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const base = `${new URL(request.url).origin}/ingest/${encodeURIComponent(String(params.orgId))}/${encodeURIComponent(String(params.projectId))}`
    const waiting = { lastAcceptedAt: null, lastRejectedAt: null, reason: null, usedKey: null, expectedKey: null }
    return HttpResponse.json<ProjectConnection>({ urls: { observability: base, webhook: `${base}/livekit/webhook` }, webhook: waiting, agent: waiting, firstCall: null, ...connectionOverride() })
  }),
  http.get<PathParams, never, AlertSetup | ApiErrorBody>(`${project}/alert-setup`, ({ params }) => (known(params) ? HttpResponse.json(alertSetup()) : fail(404, "not_found", "No such project."))),
  http.get<PathParams, never, Workspaces>("*/api/v1/workspaces", () => HttpResponse.json(workspaces())),

  // Project API keys, in memory (settings.ts).
  http.get<PathParams, never, ProjectKeys | ApiErrorBody>(`${project}/keys`, ({ params }) => (known(params) ? HttpResponse.json(projectKeys(String(params.projectId))) : fail(404, "not_found", "No such project."))),
  http.post<PathParams, { name?: string }, CreatedProjectKey | ApiErrorBody>(`${project}/keys`, async ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const { name } = await request.json()
    if (typeof name !== "string" || name.length < 1 || name.length > 64) return fail(400, "invalid_request", "name must be 1 to 64 characters")
    return HttpResponse.json(createKey(String(params.projectId), name), { status: 201 })
  }),
  http.delete<PathParams, never, ApiErrorBody>(`${project}/keys/:keyId`, ({ params }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    return revokeKey(String(params.projectId), String(params.keyId)) ? new HttpResponse(null, { status: 204 }) : fail(404, "not_found", "No such key in this project.")
  }),

  // Projects added in the console (phase 06f), kept in this browser (workspaces.ts), with web's checks.
  http.post<PathParams, Partial<ProjectCreate>, WorkspaceProject | ApiErrorBody>("*/api/v1/org/:orgId/projects", async ({ params, request }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "organisation not found")
    const body = await request.json().catch(() => null)
    const text = (v: unknown) => (typeof v === "string" ? v.trim() : "")
    const missing = (["name", "livekitUrl", "livekitApiKey", "livekitApiSecret"] as const).filter((f) => text(body?.[f]) === "")
    if (missing.length) return fail(400, "invalid_request", `missing ${missing.join(", ")}`)
    const name = text(body?.name)
    if (name.length > 64 || !/[a-z0-9]/i.test(name)) return fail(400, "invalid_request", "name must be 1 to 64 characters, with no control characters and at least one letter or digit")
    let url: URL | null = null
    try {
      url = new URL(text(body?.livekitUrl))
    } catch {
      url = null
    }
    if (!url || !["ws:", "wss:", "http:", "https:"].includes(url.protocol) || !url.hostname) return fail(400, "invalid_request", "livekitUrl must be a ws, wss, http or https URL with a host")
    // web proves the secret with one LiveKit read before storing anything. The mock reaches no
    // LiveKit, so it accepts every secret, and answers a host under the reserved .invalid domain,
    // which resolves nowhere, as web does: no LiveKit answer.
    if (url.hostname.endsWith(".invalid")) return fail(422, "livekit_unreachable", "could not reach this LiveKit URL")
    // A bucket the project brings is proven the same way, with no S3 behind the mock: every one is
    // accepted except an endpoint under .invalid.
    const own = body?.recordingBucket
    if (own) {
      const gone = (["endpoint", "bucket", "accessKeyId", "secretAccessKey"] as const).filter((f) => text(own[f]) === "")
      if (gone.length) return fail(400, "invalid_request", `missing ${gone.map((f) => `recordingBucket.${f}`).join(", ")}`)
      let endpoint: URL | null = null
      try {
        endpoint = new URL(text(own.endpoint))
      } catch {
        endpoint = null
      }
      if (!endpoint || !["http:", "https:"].includes(endpoint.protocol) || !endpoint.hostname || endpoint.pathname.replace(/\//g, "") !== "") return fail(400, "invalid_request", "recordingBucket.endpoint must be an http or https URL with a host and no path, such as https://s3.us-east-1.amazonaws.com")
      if (endpoint.hostname.endsWith(".invalid")) return fail(422, "recording_bucket_unreachable", "could not reach this S3 endpoint")
    }
    const out = await addProject(String(params.orgId), name, text(body?.livekitApiKey))
    if (out === "project_exists") return fail(409, "project_exists", "this organisation already has a project with that id; choose another name")
    return HttpResponse.json(out, { status: 201, headers: { "Cache-Control": "no-store" } })
  }),

  // Invitations and reset links, in memory (settings.ts), as web without SMTP answers them.
  http.get<PathParams, never, Invitations | ApiErrorBody>("*/api/v1/org/:orgId/invitations", ({ params }) => (knownOrg(params.orgId) ? HttpResponse.json(invitations()) : fail(404, "not_found", "No such organisation."))),
  http.post<PathParams, { email?: string; role?: string }, CreatedInvitation | ApiErrorBody>("*/api/v1/org/:orgId/invitations", async ({ params, request }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "No such organisation.")
    const { email, role } = await request.json()
    if (typeof email !== "string" || !/^[^\s@]+@[^\s@]+$/.test(email) || (role !== "Admin" && role !== "Member" && role !== "Viewer")) return fail(400, "invalid_request", "email and a role of Admin, Member or Viewer are required")
    const out = invite(email, role)
    return out === "already_member" ? fail(409, "already_member", "that email's account is already a member of this organisation") : HttpResponse.json(out, { status: 201 })
  }),
  http.delete<PathParams, never, ApiErrorBody>("*/api/v1/org/:orgId/invitations/:invitationId", ({ params }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "No such organisation.")
    return revokeInvitation(String(params.invitationId)) ? new HttpResponse(null, { status: 204 }) : fail(404, "not_found", "no such invitation in this organisation")
  }),
  http.post<PathParams, never, CreatedPasswordReset | ApiErrorBody>("*/api/v1/org/:orgId/members/:userId/password-reset", ({ params }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "No such organisation.")
    const out = passwordReset(String(params.userId))
    return out === "not_found" ? fail(404, "not_found", "no such member of this organisation") : HttpResponse.json(out, { status: 201 })
  }),

  // Members, in memory (settings.ts), with web's refusals.
  http.get<PathParams, never, Members | ApiErrorBody>("*/api/v1/org/:orgId/members", ({ params }) => (knownOrg(params.orgId) ? HttpResponse.json(members()) : fail(404, "not_found", "No such organisation."))),
  http.patch<PathParams, { role?: string }, Member | ApiErrorBody>("*/api/v1/org/:orgId/members/:userId", async ({ params, request }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "No such organisation.")
    const { role } = await request.json()
    if (role !== "Admin" && role !== "Member" && role !== "Viewer") return fail(400, "invalid_request", "role must be Admin, Member or Viewer")
    const out = changeMember(String(params.userId), role)
    if (out === "not_found") return fail(404, "not_found", "No such member of this organisation.")
    if (out === "last_admin") return fail(409, "last_admin", "An organisation needs at least one admin.")
    return HttpResponse.json(out as Member)
  }),
  http.delete<PathParams, never, ApiErrorBody>("*/api/v1/org/:orgId/members/:userId", ({ params }) => {
    if (!knownOrg(params.orgId)) return fail(404, "not_found", "No such organisation.")
    const out = changeMember(String(params.userId), null)
    if (out === "not_found") return fail(404, "not_found", "No such member of this organisation.")
    if (out === "last_admin") return fail(409, "last_admin", "An organisation needs at least one admin.")
    return new HttpResponse(null, { status: 204 })
  }),

  // Recording audio, with byte ranges for the player.
  http.get(`${project}/recordings/:recordingId`, async ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const id = String(params.recordingId)
    const media = await recordingMedia(id, request, recordingIds().has(id))
    return media ?? fail(404, "not_found", "No retained recording with this id in this project.")
  }),
]
