# Phase 06b: Members and roles

## Goal

Every `/api/v1` route allows only what the caller's organisation role permits. Admins see who belongs to their organisation, change a member's role and remove a member, and the last admin cannot be removed. An operator can add a Member or a Viewer, and reset anyone's password, from the command line. This is the second phase for decision 0027:
- 06a added sign-in.
- 06c adds project API keys and retires `VOICEGATEWAY_API_TOKEN`.
- 06d adds invitation links.

Invitations moved out of this phase to keep it buildable in one session.

## Context

- Decision 0027: one gate on every route; a signed-in user without the role an action needs is 403; ids from another project stay `not_found`. Its CLAUDE.md rules apply.
- Decision 0019: Admin, Member and Viewer, and what each may do. Roles per project stay a locked Enterprise row. Its line "invites are kept in the browser" stays true until 06d.
- `docs/specs/phase06a-sign-in.md`: `memberships` (roles stored as `admin | member | viewer`), `store.Principal`, and the `member` middleware this phase extends. The principal is loaded from Postgres on every request, so a role change applies on the next request with no session change.
- `docs/api/openapi.yaml`: every operation's `x-access` (`minimumRole`), and `getWorkspaces`, which needs a session (06a check 8).
- `dashboard/src/features/settings/settings-dialog.tsx`: the Members section and its "Role" row, which says "Admin" for everyone.
- DESIGN.md ("The Clean Room"): the member list uses shadcn components and its tokens.

## In Scope

- **Roles, in one place** (`internal/api`): a `role(minimum)` middleware after `member`, ordered Viewer < Member < Admin. A signed-in user below the minimum gets 403 `forbidden`. Every `/api/v1` route declares its minimum, read from a table in one file, and a test fails on any route without one.
  - Every read served today needs Viewer.
  - `getMembers` needs Viewer; `updateMember` and `removeMember` need Admin.
- **The interim token** names no user and has no role. Until 06c removes it:
  - It keeps the project-scoped reads it has today.
  - It gets 403 `forbidden` on organisation-scoped operations (`getMembers`, `updateMember`, `removeMember`).
  - It stays 401 on `getWorkspaces`, as in 06a.
- **New operations** in `docs/api/openapi.yaml`, each with `x-access`. Roles go over the wire as `Admin | Member | Viewer`, the contract's names, mapped from the stored lowercase values in one function.
  - `getMembers` `GET /api/v1/org/{orgId}/members`: `{items: [{userId, email, role}]}`, ordered by email. A new `Member` schema, `additionalProperties: false`.
  - `updateMember` `PATCH /api/v1/org/{orgId}/members/{userId}` with `{role}`: 200 with the member. An unknown role is 400.
  - `removeMember` `DELETE /api/v1/org/{orgId}/members/{userId}`: 204. The user's account and sessions stay. They lose access to this organisation on their next request, because the principal is reloaded each time.
  - A `userId` that is not a member of the organisation in the path is 404, also when it belongs to another organisation.
- **The last admin:** demoting or removing the only admin of an organisation is 409 `last_admin`. The change runs in one transaction that first takes `SELECT ... FOR UPDATE` on the organisation row, then counts its admins, then writes. So two admins demoting each other at once cannot leave none.
- **`voicegateway user add --role member|viewer --email <e> --org <o>`**: adds a Member or Viewer, with the password read as for `--admin` (`VOICEGATEWAY_USER_PASSWORD` or a prompt, at least 12 characters). `--admin` stays a shorthand for `--role admin`. An email that already has an account gets a membership in the new organisation, and the password is not asked again. A user already in that organisation is refused, and nothing changes.
- **`voicegateway user reset-password --email <e>`**: sets a new password (read as above, at least 12 characters) and signs that user out everywhere. This is decision 0027's "an admin resets a member's password" without SMTP, and the way back in for a lone admin who forgot theirs. A link-based reset from the console comes with 06d.
  - Signing out everywhere: `users` gains a `password_version` integer, which a reset increments. Sign-in copies it into the session, and a session is valid while its copy equals the user's. Otherwise it is deleted and answered 401. A 06a session holds no copy and is valid only while the version is 0. `scs/pgxstore` keeps no user column, so this is checked when the principal loads, not by deleting rows.
- **The contract's access text:** every operation that says `browserSessionTransport: not_selected` now names the session cookie chosen in 06a. Each operation's `x-access.minimumRole` matches the route table.
- **Console** (`dashboard/`), with `VITE_MOCK_API=off`:
  - Settings, Members lists the members from `getMembers`.
  - An Admin sees a role picker and Remove on each row, except that they cannot remove themselves. A `last_admin` answer shows as "An organisation needs at least one admin".
  - A Member or Viewer sees the list read-only.
  - The "Role" row shows the signed-in user's role from `GET /auth/session`, instead of "Admin".
  - The Invite form keeps today's browser-kept behaviour until 06d. It is shown to Admins only.
  - With the mock on, the mock serves `getMembers` and the two writes in memory.

## Out of Scope

- Invitations, invitation links and accepting them (06d).
- Project API keys, MCP access and removing `VOICEGATEWAY_API_TOKEN` (06c).
- Roles per project (Enterprise, 0019), SSO, SAML, SCIM and two-factor sign-in (Enterprise, 0017).
- Settings writes for thresholds and tags, and creating organisations or projects from the console. Member's write permission applies once those writes exist.
- Changing your own email or password, and deleting an account.

## Constraints

- No new library. Roles are a middleware and a table, not a policy engine.
- CLAUDE.md, decision 0027: every `/api/v1` route goes through the one gate; never log a password or a session id.
- Every new response validates against `docs/api/openapi.yaml` in the tests, and `docs/api/readers.json` lists the console readers of `getMembers`.
- The console uses shadcn components and the DESIGN.md tokens, and lists through `src/components/data-table.tsx` if the member list needs sorting or filtering.
- Real-thing tests: Postgres through testcontainers, `httptest` for the HTTP flows, Vitest with Testing Library for the screen.

## Acceptance Checks

1. Given a Viewer, a Member and an Admin of organisation A:
   - `updateMember` and `removeMember` are 403 for the Viewer and the Member, and succeed for the Admin (2 operations × 3 roles = 6 cases).
   - `getMembers` and every read served today answer 200 for all 3 roles.
   - With the interim token, every project-scoped read is 200, the 3 member operations are 403, and `getWorkspaces` is 401.
   (Measured by `TestRolesGateEveryRoute`.)
2. Given the router, then every `/api/v1` route has a minimum role in the table, the test names any route that does not, and each route's minimum equals its operation's `x-access.minimumRole` in `docs/api/openapi.yaml` (measured by `TestEveryRouteHasAMinimumRole`).
3. Given organisation A with 3 members, then `getMembers` returns exactly those 3, ordered by email, with roles `Admin | Member | Viewer`, and none from organisation B. `updateMember` with role `Owner` is 400 and changes nothing (measured by `TestGetAndUpdateMembers`).
4. Given an organisation with one admin, then demoting or removing them is 409 `last_admin` and nothing changes. Given two admins who demote each other in parallel, repeated 20 times, then at least 1 admin remains every time (measured by `TestLastAdminIsKept`).
5. Given a Member promoted to Admin and an Admin demoted to Viewer, then the next request with the same session cookie has the new permissions. A removed member's next request to A is 403, and their session still reads organisation B (measured by `TestRoleChangeTakesEffectOnNextRequest`).
6. Given a userId from organisation B, or a random id, then `updateMember` and `removeMember` on A answer 404 and change nothing in either organisation (measured by `TestMemberIdsStayInTheirOrganisation`).
7. Given `voicegateway user add --role viewer` for a new email, then 1 user and 1 Viewer membership exist. For an email with an account in B, it adds a membership in A without a second user. A repeat for the same organisation fails and changes nothing (measured by `TestUserAddWithRole`).
7a. Given a user signed in on two sessions, when `voicegateway user reset-password` sets a new password, then both sessions answer 401 on their next request, the old password gets 401 at sign-in, and the new one gets 204. A password under 12 characters is refused and changes nothing. A 06a session, with no password version stored, reads while the version is 0 and answers 401 after a reset (measured by `TestResetPasswordSignsOutEverywhere`).
8. Given each new operation, then every response in checks 1 to 6 validates against `docs/api/openapi.yaml` (measured by the shared response validator, called in each of those tests).
9. Given the console with the mock off, the Vitest suite `members.test.tsx` runs 5 cases (measured by that suite):
   - An Admin sees the 3 members, with a role picker and Remove on every row but their own.
   - Changing a role calls `updateMember`.
   - A `last_admin` answer shows "An organisation needs at least one admin".
   - A Viewer sees the list with no role picker, no Remove and no Invite form.
   - The Role row shows the signed-in user's role.
10. Given the log output of checks 1 to 7a, then no line contains a password or a session id (measured by `TestNoSecretInLogs`, extended).

## Manual Check

On the Compose stack behind a local HTTPS proxy:
1. Add a Viewer with `docker compose exec web voicegateway user add --role viewer`.
2. Sign in as the Viewer in a private window. Confirm they see calls and the member list, but no role picker or Invite form.
3. As the admin, try to demote yourself, and confirm "An organisation needs at least one admin".

## Open Questions

These are my recommendations; change any before building.

- `getMembers` is readable by every member, as a team list.
- The interim token gets 403 on organisation-scoped operations.
- Phase order: 06c (project API keys) before 06d (invitations), so the code's existing "until 06c" notes stay true. If invitations matter more for launch, 06d can come first; neither depends on the other.
