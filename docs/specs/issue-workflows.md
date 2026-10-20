# Persistent issue workflows

## Current evidence

Issues groups call findings and standing component checks with source links in the preview.
The current branch replaces browser-local issue decisions with scoped Postgres persistence;
standing component evaluation is still unfinished. Call tags and archive state now persist per immutable call ID in Postgres (migration
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

The next implementation adds native call finding evaluation and a project-scoped occurrence store.
Worker summary writes and findings share one transaction. Replayed evidence does not create a new
occurrence; a newly observed occurrence regresses a resolved issue even when its event timestamp is
older. Tags and archive edits never acknowledge unseen evidence. Per-rule coverage prevents missing
metrics from clearing previous findings. The issue metadata API compares both metadata and evidence
revisions, permits Member/Admin writes and Viewer/project-key reads, and rejects cross-origin writes.
Native storage, worker, authenticated HTTP and role/contract checks pass under the race detector. Go lint, SQL generation, schema/source attribution and frontend type checks pass. Issue listing, standing source evaluation,
UI integration, historical backfill and webhook delivery are still unfinished. Do not check off the
roadmap task based on this foundation.

Issue list integration now reads persisted occurrences and their decision revisions in one
repeatable-read snapshot. Literal search, normalized tags, archive state and source range filter
those records. The list includes canonical call IDs for evidence links. It explicitly reports
incomplete coverage until standing-source evaluation and historical backfill are implemented.
The approved table now saves resolve/ignore/reopen, tags, archive/restore and revision-safe Undo
through the API. Bulk actions report individual failures, Viewer controls are read-only, and
retained actions cannot edit a different project after navigation. Only the explicit development
adapter uses browser storage. List storage tests, 232 frontend tests and 88 browser/accessibility checks pass, including scoped
resolve/archive writes and reload in desktop/mobile light/dark. The obsolete browser-local
disposition API was removed. Authenticated list verification and native historical backfill checks pass under the race detector.
Historical calls are evaluated without rewriting their summaries. Settings revisions requeue call
findings; monotonic evaluation revisions reject stale attempts, and source failures use bounded
persisted backoff. A native Redis/ClickHouse/Postgres test verifies failure, retry, deduplication
and unchanged summaries. Go lint and SQL generation checks pass. The full native run exposed a first-batch assumption in the new backfill test. It now
exercises a backlog larger than one sweep; targeted native checks pass. A clean full final run
is still required before merge.
Standing findings and webhook delivery remain open; the roadmap task stays unchecked.
