# Persistent issue workflows

## Current evidence

Issues already groups call findings and standing component checks with source links. Its dispositions
use local browser storage, keyed by issue ID. Call tags use room names and archive state uses browser
storage. Production listCalls deliberately rejects tag/archived queries because metadata is absent.
These are UI previews, not shared workflows.

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

Call metadata storage is being implemented in migration 00016 with immutable call IDs, normalized
bounded tags, reversible archive state and atomic revisions. Native verification covers same-room
identity isolation, wrong project/organisation, concurrent updates, stale Undo and worker retries.
This foundation is not API/UI wiring or completed issue evaluation. All acceptance remains open
until the full paths and checks above pass.

The scoped GET/PUT call metadata API now passes native authenticated HTTP tests: Viewer and
project-key reads; Member/Admin writes; denied Viewer, key, outsider and cross-origin writes;
missing/wrong-scope IDs; malformed/oversized/invalid UTF-8 bodies; stale revisions; clear/unarchive;
and reads from a second web instance. OpenAPI role/key alignment, generated types, source attribution
and Go lint pass. The production web wiring uses the Postgres metadata store.

Next: list/detail metadata and tag/archive filters must share a transactional read with their totals.
Bind pagination to a project metadata revision so edits invalidate old snapshots instead of silently
skipping or duplicating rows. This requires checking the revision inside the list transaction, not
only before opening it. UI, issue findings/dispositions and webhook delivery remain unfinished.

List/detail integration now reads persisted tags/archive from the same immutable call row. Tag filters
match any supplied normalized tag; archived true/false and totals share the list transaction. A
transactional project metadata revision changes on metadata writes and binds snapshot/cursor
fingerprints. The list transaction rejects a generation changed after the API read, while subsequent
pages reject tokens created before an edit. Native Postgres tests passed for rows/totals, isolation,
rejected-write stability and stale-generation refusal. Native HTTP tests passed for tag/archive
queries, detail agreement and old snapshot rejection, alongside authenticated metadata permissions.
Go lint, SQL generation checks and API schema/provenance/type checks passed. UI wiring and issue
findings/dispositions remain open, so this is not a completed issue workflow.

The call metadata UI now reads and writes the scoped API. Tags, archives and revision-safe Undo
no longer use browser storage outside the explicit preview adapter. Member/Admin controls wait
for confirmed writes; Viewer controls are read-only. Bulk archives report per-item failures, and
permission loss hides retained metadata. The scoped tag catalogue reads real servable calls.
All 217 dashboard tests and lint/type checks passed in a serial run; the full browser rerun and
native race gates are still being verified. Issue evaluation/dispositions and webhook delivery
remain unfinished, so the roadmap task remains unchecked.

The next implementation adds native call finding evaluation and a project-scoped occurrence store.
Worker summary writes and findings share one transaction. Replayed evidence does not create a new
occurrence; a newly observed occurrence regresses a resolved issue even when its event timestamp is
older. Tags and archive edits never acknowledge unseen evidence. Per-rule coverage prevents missing
metrics from clearing previous findings. The issue metadata API compares both metadata and evidence
revisions, permits Member/Admin writes and Viewer/project-key reads, and rejects cross-origin writes.
Native storage, worker and HTTP checks are being verified. Issue listing, standing source evaluation,
UI integration, historical backfill and webhook delivery are still unfinished. Do not check off the
roadmap task based on this foundation.
