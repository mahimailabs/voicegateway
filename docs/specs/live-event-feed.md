# Live operations: worker change feed

## Scope

Continue sampled live operations (#429) with the worker-pushed feed required by issue #320 and
architecture decision 0007. Preserve the approved console and the existing SFU and worker sample
cadences. Resource instrumentation remains an additional live-operations deliverable; this feed
alone does not complete CPU/memory collection.

## Contract

Workers publish a project-scoped invalidation after durable call-event or layer-sample storage.
Redis pub/sub carries only an invalidation hint, never source payloads, transcripts or credentials.
Publishing is bounded and best effort: a live-feed failure must not fail durable ingestion. Existing
snapshot polling remains the recovery path, and missed notifications do not mean lost telemetry.

A new authenticated GET /api/v1/org/{orgId}/project/{projectId}/live-events streams SSE hints.
It uses the same project/role checks as the snapshot APIs. Each short connection expires so the
next request rechecks credentials and membership. The hint grants no data access: every resulting
snapshot read independently rechecks authorization. Reconnect always refreshes snapshots because
pub/sub is not a replay log. Redis disconnection closes the stream instead of reporting it healthy.
Browser teardown and project changes close the old connection; hidden tabs suspend it. Source
freshness remains distinct from connection health, since a connected web server does not prove a
worker or monitored endpoint is reporting.

## Acceptance

1. A stored call event and a stored SFU/worker sample produce a hint for that project only.
2. Failed durable writes produce no hint. Redis failure never prevents durable ingest completion.
3. Authenticated viewers can subscribe only to their authorized project. No credentials in URLs.
4. Disconnect and reconnect are visible; reconnect refetches snapshots without replaying old rooms.
5. Leaving the project or hiding the tab closes its subscription. Permission loss clears telemetry.
6. Missing source samples still age to stale even with a healthy stream. No missing value becomes zero.
7. Real Redis tests verify isolation, disconnection, cleanup and publication. API tests verify the
   security boundary and streaming lifecycle; browser tests verify invalidation and recovery.

Required checks: make test, make lint, make api-check, make dashboard-e2e and pre-commit hooks.

## Verification

- Acceptance 1-2: TestSinkAcksOnlyAfterInsert, TestLiveHintFollowsDurableSourceRead,
  TestLiveNativeSamplesReachStream and TestLivePublishUnavailableIsBounded pass. The native test
  uses LiveKit 1.13.3, ClickHouse and Redis, then a real Prometheus HTTP endpoint; notifications
  precede authenticated snapshot reads only after storage. Source failure retains the prior sample.
- Acceptance 3: TestLiveStreamSecurityAndFailure plus the existing route-role and project-key
  contract checks pass. Failed subscription errors do not expose Redis connection details.
- Acceptance 4-5: live-updates.test.ts and live-provider.test.tsx cover refresh/renewal, denial,
  late in-flight answers, project switching, hidden tabs and cleanup. Native Redis and HTTP tests
  cover lost connections and client cancellation.
- Acceptance 6: existing source-age/disconnect regressions continue passing. Stream state adds
  a separate badge and does not overwrite source observations or freshness.
- Acceptance 7: full Docker-backed Go race suite, 202 dashboard tests, lint/TypeScript, source and
  generated API checks, and 72 Playwright/accessibility checks pass. A concurrent UI-only run
  timed out in existing call-detail tests while Go containers were busy; the full sequential
  make test passed without changing test timeouts. The native end-to-end test also passes separately.

Connections renew after 15 seconds; browser connections time out after 30 seconds and retry after
3 seconds. Notification publication has a 250 ms budget, a separate Redis pool and no retries.
The generated hey-api SSE parser handles streaming; no handwritten SSE parser is added.
