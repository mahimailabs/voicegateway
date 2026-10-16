import { http, HttpResponse, type PathParams } from "msw"
import type { Call, CallPage, Error as ApiErrorBody } from "@/lib/api-types"
import { orgs } from "@/components/workspace-switcher"
import { capturedCalls } from "./fixtures"
import { summarize } from "./summaries"
import { matchesFilters, parseFilters, sortCalls, totals } from "./calls-query"

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

    const matching = sortCalls(capturedCalls().map(summarize).filter((c) => matchesFilters(c, filters)), filters)
    const items = matching.slice(start, start + limit)
    const next = start + limit < matching.length ? `${start + limit}:${bound}` : null
    return HttpResponse.json<CallPage>({ items, page: { nextCursor: next, snapshot: SNAPSHOT }, totals: totals(matching) })
  }),

  http.get<PathParams, never, Call | ApiErrorBody>(`${project}/calls/:callId`, ({ params }) => {
    if (!known(params)) return fail(404, "not_found", "No such project.")
    const call = capturedCalls().find((c) => c.id === params.callId)
    return call ? HttpResponse.json(call) : fail(404, "not_found", "No such call in this project.")
  }),
]
