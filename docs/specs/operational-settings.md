# Operational project settings

## Audit and scope

The approved Thresholds form saves to browser storage. The API contract declares settings reads,
but no Go handler implements them. Call queries use a global answer budget instead of project
configuration. Members may change project settings; Viewers and project API keys may only read
(decisions 0019 and 0027). Account, membership, invitation and key actions already use server APIs.

Persist project thresholds and use them in actual call queries and console diagnosis. Keep the
approved form. Tags/archive/issue state and alert channels follow in their dedicated goal steps.
Retention and storage are read-only facts of the implemented deployment, not pretend saved options.
Embed authorization remains a separate roadmap item.

## Contract

- Postgres owns thresholds per organisation/project. Defaults are 1500/2000/1000 ms.
- Each value must be finite and between 100 and 60000 milliseconds, matching the form's bounds.
- Viewer reads and Member writes go through the existing role gate. Browser mutations retain
  same-origin protection. Project keys cannot mutate settings.
- Every write carries the last read revision. Conflicts return 409, including stale Undo actions.
  A successful write atomically advances revision and returns the stored settings.
- Reads, queries and UI use the same project thresholds. Permission loss clears retained settings;
  scope changes cancel old requests. Saves invalidate affected project queries after persistence.
- Storage and retention fields describe actual implemented policy, with null for unavailable facts.

## Acceptance

1. Real Postgres persistence survives reopening the store and isolates projects and organisations.
2. Validation rejects malformed, missing, non-finite and out-of-range values; failures preserve prior settings.
3. Concurrent writes and stale Undo cannot overwrite newer settings.
4. Auth, role, project isolation and cross-origin tests cover reads and writes.
5. Changing thresholds changes real call filtering/statistics and diagnosis/join/TTS checks consistently.
6. The existing form supports pending, error, conflict and read-only states without reporting false success.
7. Required root checks, meaningful integration/browser regressions and review pass before completion.

Reuse pgx, sqlc, goose, the existing auth gate, net/http CrossOriginProtection, generated API types,
TanStack Query and existing shadcn controls. No new framework or legacy browser-storage migration.

## Implementation evidence (in progress, 2026-10-20)

- Real Postgres tests cover persisted values, organisation/project isolation and concurrent revision conflicts.
- HTTP tests cover malformed/missing/out-of-range fields, stale writes, role gates, project-key read scope and cross-origin write rejection. Targeted Go race checks passed.
- Call lists and statistics now read the project's persisted answer threshold. The interim global answer-budget setting is removed. A native-storage regression verifies fractional thresholds change both results consistently and rejects snapshots bound to the prior settings revision.
- API schema validation and provenance checks passed before the latest totals-schema correction; repeat the full checks with the finished UI.
- Frontend persistence, scoped invalidation, denial clearing, conflict handling and stale Undo are implemented. Background refresh preserves unsaved drafts and their original revision. The dashboard suite passed 204 tests before the resource baseline update.
- The original 72 browser checks passed. The updated baseline includes new authenticated settings-session and browser Save/Undo checks; final full checks and review remain pending. This task is not complete.
