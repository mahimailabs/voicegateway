# Persistent issue workflows

## Current evidence

Issues groups call findings and standing component checks with source links in the preview. Its
resolve/ignore dispositions still use browser storage keyed by issue ID and are not a shared
workflow. Call tags and archive state now persist per immutable call ID in Postgres (migration
00016), and listCalls applies persisted tag/archive filters with consistent totals.

## Required end state

1. Read real findings from recorded call/turn evidence and latest supported component samples.
   Reuse the same rule definitions for subsequent webhook alerts. Distinguish event time from
   standing state, unknown from healthy, and a missing source from recovery.
2. Persist issue metadata by organisation, project and stable issue/rule identity. Persist call
   metadata by organisation, project and immutable call UUID, never display room name.
3. Viewer and scoped API keys read only. Member/Admin mutations go through the existing role gate
   and same-origin protection. Reject wrong scope and missing identities without leaking data.
4. Tags are bounded, normalized, unique values. Archive is reversible and independent of resolving
   or ignoring an issue. Resolving stores the latest occurrence observed, so a later occurrence can
   regress; an archive must never falsify underlying evidence or suppress alert evaluation silently.
5. Writes compare revisions atomically. Stale saves and Undo fail with 409. Bulk actions either
   commit as one transaction or explicitly report per-item outcomes; do not show partial success as
   complete success. Refresh/cancel old scope and denied data using the existing query conventions.
6. Search, tags and archive filter actual persisted records and totals. Cursor/snapshot semantics
   must remain consistent when metadata changes. Evidence links resolve to the permitted real call.
7. Preserve the approved table, filters, drawer and menus. Replace local writes with real API writes;
   show pending/error/permission states. No browser-storage migration or fallback fake success.

## Acceptance evidence required

- Native Postgres: persistence/reopen, same room name in different calls/projects, isolation,
  concurrent revisions, rejected stale writes, reversible archives and tag normalization.
- HTTP: roles, project keys, cross-origin, malformed/oversized requests, wrong project and missing ID.
- Real call evidence: findings agree with source durations, missing measurements remain unknown,
  standing source failures do not become healthy, and regression follows new evidence.
- Queries: search/tag/archive results and counts agree; snapshot changes cannot silently duplicate or
  skip rows; list/detail metadata agrees.
- Browser: another session sees saved state, reload preserves it, failure preserves the user's intent,
  Viewer has no mutation controls, project switching cannot retain or mutate prior-project metadata.
- Full root race/test/lint/schema/provenance/browser gates, focused PR review and merge.

Settings and alerts dependencies remain explicit. Completing metadata alone does not complete the
issue-workflow task, and completed issue persistence does not prove webhook delivery/retries.

## Implementation progress

Call metadata is implemented through scoped GET/PUT APIs and a scoped tag catalogue. Member/Admin
writes compare revisions atomically; Viewer and project keys read only. Stale saves and Undo fail
with 409. Tags/archive are independent of worker retries and reused room names. List/detail reads
agree, and project metadata revisions invalidate pagination after edits. The approved UI waits
for confirmed saves, reports partial bulk failures, and clears denied/deleted data across query
observers. Project changes prevent retained actions from mutating the previous project.

Validation on PR #435's implementation head 23e97377: full Docker-backed Go race suite, Go lint and
SQL generation checks, API schema/provenance/generated-type checks, 225 frontend tests, TypeScript
and all 84 browser/accessibility checks passed. Browser tests cover HTTP tag/archive writes and
reload in desktop/mobile light/dark. Native tests cover independent web instances, scope,
permissions, cross-origin refusal, malformed input, concurrent revisions, stale Undo, filters and
snapshot invalidation. The CLI-key integration fixture now includes the metadata dependency,
matching production wiring. Automated review found stale documentation, corrected here.

Issue findings/dispositions and webhook delivery remain unfinished. Completing call metadata does
not complete the issue-workflow roadmap task, which remains unchecked.

## Deployment

Migration 00016 takes schema locks while adding checked columns and building call metadata indexes.
For large installations, schedule a maintenance window and stop the web and worker processes before
running migrations, then start them after migration success. This migration is transactional; it
does not claim a zero-downtime upgrade. Concurrent index construction alone would not remove the
column/constraint locking, so the atomic migration is retained with this explicit operating rule.

The tag catalogue scans servable calls within one project to collect distinct tags. Its query does
not use the tag GIN index. Observe its query duration as projects grow; the index supports membership
filters, not catalogue aggregation.
