import type { QueryClient } from "@tanstack/react-query"
import { getLiveEvents } from "@/lib/api-types"
import { ApiError, accessDenied } from "@/lib/api"

export type LiveConnection = "connecting" | "connected" | "disconnected" | "paused"
type Scope = { org: string; project: string }

// Use the generated SDK's SSE parser. Each renewal authenticates again; every initial refresh
// repairs missed pub/sub messages from snapshots. No second telemetry store lives in the browser.
export async function watchLiveUpdates(scope: Scope, client: QueryClient, state: (value: LiveConnection) => void, signal: AbortSignal) {
  const matches = (key: readonly unknown[]) => {
    const project = key[1] as Scope | undefined
    return project?.org === scope.org && project?.project === scope.project
  }
  let refreshTimer: ReturnType<typeof setTimeout> | undefined
  const refresh = () => {
    if (refreshTimer !== undefined) return
    refreshTimer = setTimeout(() => {
      refreshTimer = undefined
      if (!signal.aborted) void client.invalidateQueries({ predicate: (query) => matches(query.queryKey) }, { cancelRefetch: false })
    }, 200)
  }
  const sleep = () => new Promise<void>((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve() }
    const timer = setTimeout(done, 3000)
    signal.addEventListener("abort", done, { once: true })
    if (signal.aborted) done()
  })
  state("connecting")
  try {
    while (!signal.aborted) {
      let renew = false
      const connection = AbortSignal.any([signal, AbortSignal.timeout(30000)])
      const result = await getLiveEvents({
        path: { orgId: scope.org, projectId: scope.project },
        signal: connection,
        cache: "no-store",
        sseMaxRetryAttempts: 1,
        fetch: async (input, init) => {
          const response = await fetch(input, init)
          if (signal.aborted) throw new DOMException("Aborted", "AbortError")
          if (!response.ok) {
            const error = new ApiError(response.status, "live_unavailable", "Live updates unavailable")
            if (accessDenied(error)) {
              // Cancel old authorized requests before clearing: their late answers must not
              // restore telemetry after this denial.
              await client.cancelQueries({ predicate: (query) => matches(query.queryKey) })
              // Hints contain no telemetry, but a denied stream also invalidates retained reads.
              for (const query of client.getQueryCache().findAll({ predicate: (query) => matches(query.queryKey) })) {
                query.setState({ data: undefined, dataUpdatedAt: 0, error, status: "error" })
              }
            }
            throw error
          }
          if (!response.headers.get("Content-Type")?.includes("text/event-stream")) throw new Error("No live stream answered")
          return response
        },
        onSseEvent: ({ event }) => {
          if (signal.aborted) return
          if (event === "refresh") { state("connected"); refresh() }
          if (event === "renew") renew = true
        },
        onSseError: () => { if (!signal.aborted) state("disconnected") },
      })
      for await (const _event of result.stream) { if (signal.aborted) break }
      if (signal.aborted) break
      if (!renew) { state("disconnected"); await sleep() }
    }
  } finally {
    if (refreshTimer !== undefined) clearTimeout(refreshTimer)
  }
}
