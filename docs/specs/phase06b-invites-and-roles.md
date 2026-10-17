# Phase 06b: Invitations, members and roles

## Goal

An admin invites a teammate by email, with a role, and copies the invitation link from Settings. The teammate opens the link, sets a password (or signs in, if they already have an account) and joins the organisation. Admins see the members, change a role and remove someone, and every `/api/v1` route allows only what the caller's role permits. This is the second of three phases for decision 0027: 06a added sign-in, and 06c adds project API keys and retires `VOICEGATEWAY_API_TOKEN`.

## Context

- Decision 0027: invitations (single-use, a random token stored hashed, 7 days, one organisation and role, copied by hand without SMTP), the one gate on every route, and the rules it added to CLAUDE.md.
- Decision 0019: the roles Admin, Member and Viewer, and what each may do. Roles per project stay a locked Enterprise row.
- `docs/specs/phase06a-sign-in.md`: users, memberships, sessions, sign-in limits, `store.Principal` (loaded from Postgres on every request) and the `member` middleware this phase extends.
- `docs/api/openapi.yaml`: `getInvitations` (line 835, `minimumRole: Admin`) and every operation's `x-access`.
- `dashboard/src/features/settings/settings-dialog.tsx`: the Members section, which today keeps invites in the browser (`vg.invites`).
- DESIGN.md ("The Clean Room"): the accept-invitation screen and the Members section use shadcn components and its tokens.

## In Scope

- **Postgres** (one goose migration, sqlc):
  - `invitations`: id, organisation, email (case-folded), role, SHA-256 token hash (unique), invited by, created at, expires at, accepted at, revoked at.
  - A pending invitation is one that is not accepted, not revoked and not expired. At most one pending invitation per organisation and email; inviting again replaces it, so the old link stops working.
- **Roles, in one place** (`internal/api`): a `role(minimum)` middleware next to `member`, ordered Viewer < Member < Admin. A signed-in user below the minimum gets 403 `forbidden`. Every route declares its minimum, and a test fails if a route in the router has none.
  - Every read served today needs Viewer.
  - `getInvitations` and every write in this phase need Admin.
  - The interim token keeps every read and is refused every write with 403 `forbidden` ("sign in as an admin"), because it names no user to record as the inviter.
- **New operations** in `docs/api/openapi.yaml`, under a new tag "Console writes", each with `x-access`:
  - `getInvitations` (exists): pending invitations, now with `id`, `email`, `role`, `expiresAt`. Accepted, revoked and expired ones are left out.
  - `createInvitation` `POST /api/v1/org/{orgId}/invitations` with `{email, role}`: 201 with the invitation and its `link`. The link is returned only here, once. A malformed email or unknown role is 400. Inviting an existing member is 409 `already_member`.
  - `revokeInvitation` `DELETE /api/v1/org/{orgId}/invitations/{invitationId}`: 204. An id from another organisation is 404.
  - `getMembers` `GET /api/v1/org/{orgId}/members`: `{items: [{userId, email, role}]}`. Any member may read it (Viewer), as a team list.
  - `updateMember` `PATCH /api/v1/org/{orgId}/members/{userId}` with `{role}`: 200.
  - `removeMember` `DELETE /api/v1/org/{orgId}/members/{userId}`: 204. It also deletes the removed user's sessions only if they belong to no organisation afterwards.
  - The last admin of an organisation cannot be demoted or removed: 409 `last_admin`. The check and the change run in one transaction that locks the organisation's admin rows, so two admins demoting each other at once cannot leave none.
- **The link and accepting it** (`internal/auth`, outside `/api/v1`, like sign-in):
  - The token is 32 random bytes, base64url. The link is `<public URL>/invite#<token>`. The token sits in the fragment, so it never reaches a server or proxy log.
  - `GET /auth/invitation` with the token in an `X-Invitation-Token` header returns `{email, organisation name, role, hasAccount}` for a pending invitation, or 404 `invitation_invalid`, the same for unknown, used, revoked and expired.
  - `POST /auth/invitation/accept` with `{token, password}`:
    - With no account for that email, it creates the user with that password (at least 12 characters, as `project add` requires), adds the membership, and signs them in (204 and the session cookie).
    - With an account, the password must be that account's. It goes through the same atomic sign-in reservation and limits as `POST /auth/sign-in`, then adds the membership and signs them in.
    - The invitation is marked accepted in the same transaction that adds the membership, with a conditional update, so a second accept, even a parallel one, gets 404 `invitation_invalid`.
    - It follows the same HTTPS rule as sign-in (`https_required`).
- **Console** (`dashboard/`), with `VITE_MOCK_API=off`:
  - `/invite#<token>` shows an accept screen: the organisation and role, then a password field ("Set a password", or "Sign in to join" when `hasAccount`). It shows `invitation_invalid` as "This invitation is no longer valid", and `https_required`, 401 and 429 as the sign-in screen does.
  - Settings, Members: the member list from `getMembers`, with a role picker and Remove for admins. The Invite form calls `createInvitation` and shows the link once with a copy button (the existing `CopyCommand`). Pending invitations come from `getInvitations`, each with Revoke. `last_admin` and `already_member` show as messages.
  - The "Role" row shows the signed-in user's real role instead of "Admin".
  - A Member or Viewer sees the member list read-only, with no Invite form.
  - The browser-kept `vg.invites` is removed. With the mock on, the mock serves these reads and writes in memory.

## Out of Scope

- Sending email, SMTP settings and password reset by email (later; 0027 leaves them to a spec).
- Project API keys, MCP access and removing `VOICEGATEWAY_API_TOKEN` (06c).
- Roles per project (Enterprise, 0019), SSO, SAML, SCIM and two-factor sign-in (Enterprise, 0017).
- Creating organisations or projects from the console, and settings writes for thresholds and tags. Member's write permission is enforced as soon as those writes exist; no such write is served yet.
- Changing your own email or password.

## Constraints

- Library first: tokens from `crypto/rand`, hashes from `crypto/sha256`, compared with `subtle.ConstantTimeCompare` where a comparison is not a database lookup by hash. Sessions, passwords and limits reuse 06a's code; no second copy.
- CLAUDE.md, decision 0027: never log a password, a session id or an invitation token; every `/api/v1` route goes through the one gate; credentials are compared in constant time.
- Every new response validates against `docs/api/openapi.yaml` in the tests, and `docs/api/readers.json` lists the console readers of the new operations.
- The console uses shadcn components and the DESIGN.md tokens. Page state that belongs in the URL (the open Settings section) stays in it.
- Real-thing tests: Postgres through testcontainers, `httptest` for the HTTP flows, Vitest with Testing Library for the screens.

## Acceptance Checks

1. Given an admin of organisation A, when they create an invitation for `new@example.com` as Viewer, then the answer is 201 with a link, `getInvitations` lists it with its 7-day expiry, the stored row holds a SHA-256 hash and not the token, and the token appears in no log line (measured by `TestCreateInvitation`).
2. Given that link, when the invitee accepts it with a 12-character password, then they get 204 and a session cookie, 1 user and 1 Viewer membership in A exist, and the invitation leaves `getInvitations`. A second accept of the same token, and 10 parallel accepts of a fresh one, give exactly 1 success; the rest get 404 `invitation_invalid` (measured by `TestAcceptInvitationOnce`).
3. Given an invitation for an email that already has an account in organisation B, when it is accepted with that account's password, then the user belongs to A and B with one account. With a wrong password it is 401 and no membership is added, and the 11th wrong try within 15 minutes is 429 (measured by `TestAcceptInvitationExistingAccount`).
4. Given an expired invitation, a revoked one, one replaced by a newer invitation to the same email, and a random token, then `GET /auth/invitation` and accept answer 404 `invitation_invalid` with identical bodies for all 4 (measured by `TestInvitationInvalidLooksAlike`).
5. Given a Viewer, a Member and an Admin of A, then `createInvitation`, `revokeInvitation`, `updateMember`, `removeMember` and `getInvitations` are 403 for the Viewer and the Member and succeed for the Admin (5 operations × 3 roles = 15 cases). Every read served today answers 200 for all 3 roles. With the interim token, every read is 200 and every write is 403 (measured by `TestRolesGateEveryRoute`).
6. Given the router, then every `/api/v1` route declares a minimum role, and the test lists any that does not (measured by `TestEveryRouteHasAMinimumRole`).
7. Given an organisation with one admin, then demoting or removing them is 409 `last_admin`. Given two admins who demote each other in parallel 20 times, then at least 1 admin remains every time (measured by `TestLastAdminIsKept`).
8. Given a Member promoted to Admin, or an Admin demoted to Viewer, then their next request with the same session cookie gets the new permissions. A removed member's next request to A is 403 (measured by `TestRoleChangeTakesEffectOnNextRequest`).
9. Given ids from organisation B, then `revokeInvitation`, `updateMember` and `removeMember` called on A with them answer 404 and change nothing in B (measured by `TestMemberIdsStayInTheirOrganisation`).
10. Given each new operation, then all its responses in checks 1 to 9 validate against `docs/api/openapi.yaml` (measured by the contract check in `TestCreateInvitation` and the shared response validator).
11. Given the console with the mock off, then the accept screen shows the organisation and role, accepts a password and opens the console on 204, and shows "This invitation is no longer valid" for `invitation_invalid`. In Settings, an Admin sees the member list, invites someone and sees the link once, and revokes an invitation; a Viewer sees the list with no Invite form and no role picker (measured by the Vitest suites `accept-invitation.test.tsx` and `members.test.tsx`, 6 cases).
12. Given the log output of checks 1 to 9, then no line contains a password, a session id or an invitation token (measured by `TestNoSecretInLogs`, extended).

## Manual Check

On the Compose stack behind a local HTTPS proxy: sign in as the first admin, invite a second address as Viewer, open the copied link in a private window, set a password, and confirm the Viewer sees calls but no Invite form. Then demote the first admin and confirm `last_admin` shows.

## Open Questions

These are my recommendations; change any before building.

- `getMembers` is readable by every member (a team list), while `getInvitations` stays Admin-only, as the contract already says.
- The interim token keeps reads and loses writes. 06c removes it.
- The token goes in the link's fragment, not its path or query, so it never reaches a log. The cost: the console must send it in a header, and a link pasted without its fragment does nothing.
- Inviting an email again replaces its pending invitation, so only the newest link works.
