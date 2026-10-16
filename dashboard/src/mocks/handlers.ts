import { http, HttpResponse, type PathParams } from "msw"
import type { Call, CallPage, Error as ApiErrorBody } from "@/lib/api-types"
import { orgs } from "@/components/workspace-switcher"
import { capturedCalls } from "./fixtures"
import { summarize } from "./summaries"

// The mock API: the contract's paths (docs/api/openapi.yaml) answered from the captured fixtures, so
// the console fetches exactly as it will from web. Only the paths a page has moved to are here.

// Any origin: the console calls its own origin, tests call a test host.
const project = "*/api/v1/org/:orgId/project/:projectId"

const fail = (status: number, code: ApiErrorBody["code"], message: string) =>
  HttpResponse.json<ApiErrorBody>({ code, message, requestId: `mock-${crypto.randomUUID()}` }, { status })

// A project the console does not know is not found, as on the server.
function known(params: PathParams) {
  return orgs.some((o) => o.id === params.orgId && o.projects.some((p) => p.id === params.projectId))
}

// The fixtures never change while the page is open, so one snapshot names them all.
const SNAPSHOT = "captured"

export const handlers = [
  http.get<PathParams, never, CallPage | ApiErrorBody>(`${project}/calls`, ({ params, request }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const q = new URL(request.url).searchParams
    const limit = Number(q.get("limit") ?? 25)
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) return fail(400, "invalid_request", "limit must be 1 to 100.")
    const cursor = q.get("cursor")
    if (cursor !== null && q.get("snapshot") !== SNAPSHOT) return fail(400, "invalid_request", "cursor needs the snapshot it came with.")
    const start = cursor === null ? 0 : Number(cursor)
    if (!Number.isInteger(start) || start < 0) return fail(400, "invalid_request", "Unknown cursor.")

    const all = capturedCalls()
      .map(summarize)
      .sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt))
    const items = all.slice(start, start + limit)
    const next = start + limit < all.length ? String(start + limit) : null
    return HttpResponse.json<CallPage>({ items, page: { nextCursor: next, snapshot: SNAPSHOT } })
  }),

  http.get<PathParams, never, Call | ApiErrorBody>(`${project}/calls/:callId`, ({ params }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const call = capturedCalls().find((c) => c.id === params.callId)
    return call ? HttpResponse.json(call) : fail(404, "not_found", "No such call in this project.")
  }),
]
