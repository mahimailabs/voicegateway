# Account settings

## Audit and scope

The broader operational-settings roadmap includes account profile persistence. The Account pane
currently displays a permanent Name: Not set row. Email and role are already read from the
signed-in session; theme is explicitly kept in this browser. Users have no display-name column
or profile mutation. Preserve the existing Account layout and session/authentication mechanisms.

## Implementation contract

- Store a nullable display name on the existing user account, independent of organisation and
  project. Empty trimmed input clears it; nonempty names are at most 100 Unicode code points.
  Reject invalid UTF-8 and control characters. Never interpret the name as HTML or an identity key.
- Read and update only the signed-in account through the existing auth service. The request does
  not accept a user ID, email, role, organisation or project. Project API keys are not user sessions.
- Updates require same-origin protection, bounded JSON and the current profile revision. Compare
  and increment the revision atomically; a stale tab receives 409 without overwriting newer data.
- Session reads expose the name/revision, and the Account Name row uses the existing shadcn input
  and save controls. Save succeeds only after persistence. Failed and conflicting saves retain
  the draft; an explicit reload adopts the newer stored profile.
- Email/role remain read-only identity facts. Theme remains browser-local as currently labelled;
  this change does not silently change its scope or claim retention/integrations complete.

## Acceptance

1. Native Postgres tests prove persistence, clearing, validation and stale-write protection.
2. Signed-in Viewer, Member and Admin can edit only their own profile. Unauthenticated, expired
   session, bearer-key-only and cross-origin writes fail without changing stored data.
3. User A cannot target user B through request fields; unknown fields are rejected.
4. The existing Account pane saves, reloads, preserves failed drafts and shows conflicts honestly.
   A profile persists after sign-out/sign-in and a project switch does not select a different user.
5. Required root tests, browser/accessibility checks, lint and review pass before merge.

This is a subsequent settings slice based on #432. It must not mark the wider roadmap settings
item complete while integrations, recording/retention and embed dependencies remain unfinished.

## Verification (2026-10-20)

- Acceptance 1: native Postgres persistence/reopen, per-account isolation, Unicode/control validation,
  concurrent compare-and-set, stale writes and clear passed with the race detector.
- Acceptance 2 and 3: native auth tests passed for Viewer/Member/Admin, revoked session, bearer-only,
  cross-origin, bounded/invalid JSON and attempted targeting of another account. Fresh sign-in reads
  the saved profile from both profile and session endpoints.
- Acceptance 4: component tests prove deferred success, draft retention on conflict, explicit reload
  and cancellation of an in-flight read after session expiry. The profile API has no project selector;
  its identity is exclusively the signed-in account. Authenticated browser proof remains pending.
- Acceptance 5: the full Go race suite and 210 dashboard tests passed. All 80 preview browser/accessibility checks and lint/types passed;
  authenticated browser proof and PR review are pending. Existing preview browser tests do not prove authenticated profile editing.
