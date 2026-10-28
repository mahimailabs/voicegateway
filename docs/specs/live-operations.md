# Live operations: observed SFU and worker snapshots

## Audit and scope

Phase 05 already samples ListRooms every 10 seconds, worker Prometheus gauges every 15 seconds,
and Cloud agents every 60 seconds. Failed reads retain the last successful observations and an
error class. The console had infinite-stale queries, no automatic refresh, no worker metrics view,
and a Live timeline that repeatedly shifted historical rooms onto today's clock.

This pass uses the existing authenticated, project-scoped read APIs with TanStack Query polling.
It adds no direct browser connection to LiveKit or worker endpoints. Query keys and request paths
capture the same scope; abandoned requests are cancelled, requests time out after 10 seconds,
and focus/reconnect refresh the snapshot. Polling pauses in hidden tabs. All endpoint authorization
is re-evaluated on each read. Access-denied responses hide previously cached telemetry.

## Acceptance

1. SFU, worker and Cloud deployment snapshots refresh at their actual source cadence.
2. Disconnect retains explicitly last-observed values; reconnect replaces them with new snapshots.
3. Three missed sampling intervals mark a source stale even if the HTTP API still answers 200.
4. A worker or room absent from a newer successful read is "No longer listed". Failed reads never
   imply an ended call, idle worker or zero jobs. Last-observed values remain timestamped.
5. Live uses original timestamps once per room SID. Bars stop at the last sample, not a guessed
   disconnect time. Historical observations are not replayed or counted as currently active.
6. Real worker gauges are shown independently of Cloud agent discovery. Never-answering endpoints
   and partial endpoint failures remain visible. Worker load is not labelled CPU utilization.
7. The checks below pass and the plan distinguishes this implementation from remaining work.

## Sources and limits

Existing source attribution and native versions remain in docs/api/upstream-sources.md.
LiveKit's configurable load_fnc means lk_agents_worker_load cannot be treated as CPU utilization.
TanStack Query's native refetchInterval, abort signal and reconnect handling provide the update
lifecycle without a second transport or handwritten retry system.

This is sampled monitoring, not a worker-pushed event stream. Push delivery, exact participant
transition timestamps, SFU host CPU/memory and self-hosted worker CPU/memory collection remain
separate work. Capacity runners and their preview topology are unchanged. Unknowns stay unknown.

## Checks

make test, make lint, make api-check, make dashboard-e2e. Targeted regressions cover query scope,
automatic refresh, disconnect/reconnect, source-age expiry, absence versus failure and permission
loss. Existing Go integration tests cover successful and failed native SFU/worker sampling.

The existing read handlers and browser requests use no-store to prevent a cache hit from masking
a revoked permission or an old snapshot. The native integration read helper asserts this header
for SFU, workers and Cloud agents, including unavailable-source answers.
