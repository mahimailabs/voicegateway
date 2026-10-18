# Phase 06e: Instance API

## Goal

An operator's program, holding one instance token, creates an organisation, invites its first admin and reads whether that admin has joined, all over HTTP. A retried request never creates a second organisation, a second open invitation or a second email. This is decision 0028, built on 06d's invitations.

## Context

- Decision 0028: a hosted workspace is one organisation on a shared instance; the instance API is Enterprise code in `ee/instance`, off unless `VOICEGATEWAY_INSTANCE_TOKEN` is set.
- Decision 0017: "Organisation and instance management API" and "multiple organisations" are Enterprise rows. `ee/README.md` and `ee/LICENSE` cover code under `ee/`.
- Decision 0027 and its CLAUDE.md rules: one access gate on every `/api/v1` route, credentials compared in constant time, no secret in a log line.
- `docs/specs/phase06d-invitations.md` (06d, #407): the `invitations` table, hashed tokens, 7-day expiry, the link base from `VOICEGATEWAY_PUBLIC_URL`, the token in the link's fragment, `invited_by` nullable for this API, and `invite.Create` with its `IdempotencyKey`, which this phase calls. An empty base URL is `ErrInvalid`.
- `internal/api/roles.go`: `routeAccess`, `handle`, `allow` and the scopes `project`, `organisation`, `principal`.
- `migrations/postgres/00001_projects.sql`: organisation ids match `^[a-z0-9][a-z0-9-]{0,62}$`.

## In Scope

- **The instance token.** `VOICEGATEWAY_INSTANCE_TOKEN`, at least 32 characters, read once at start. Unset, every `/api/v1/instance` route answers 404 `not_found`, as if it did not exist. Web keeps only its SHA-256 and compares a request's Bearer against it with `crypto/subtle.ConstantTimeCompare`. It is never logged, and config errors name the variable, never its value.
- **A fourth scope, `instance`**, in `routeAccess`, with the minimum role `Instance`:
  - A request with the instance token is an instance principal. It names no user and no organisation.
  - An instance principal may call `instance` routes only; every other `/api/v1` route answers it 403 `forbidden`.
  - A session or a project API key on an `instance` route answers 403 `forbidden`. No credential or an invalid token answers 401.
  - The contract's `x-access.minimumRole` is `Instance` on these operations, and `TestEveryRouteHasAMinimumRole` covers them.
- **Operations** in `docs/api/openapi.yaml`, tagged `Instance`, each with `x-access` and `x-source`, `additionalProperties: false` on every new schema. Handlers live in `ee/instance`; `internal/app` wires them; `internal/api` registers them through `handle`.
  - `putOrganisation` `PUT /api/v1/instance/organisations/{orgId}` with `{name}`: 201 with the organisation when created, 200 when it already exists. A different name renames it. An id that breaks the pattern, or an empty name, is 400 `invalid_request`.
  - `getOrganisation` `GET /api/v1/instance/organisations/{orgId}`: 200 with `{id, name, createdAt, members, admins, pendingInvitations}`, the last three counts. Unknown id is 404.
  - `createOrganisationInvitation` `POST /api/v1/instance/organisations/{orgId}/invitations` with `{email, role}`: creates a 06d invitation with no inviting user.
    - The request must carry an `Idempotency-Key` header (1 to 200 printable characters), passed to 06d's `invite.Create` as `IdempotencyKey`. Without it the answer is 400 `invalid_request`. A Cloud control plane sends its checkout session id.
    - 201 with `{invitationId, email, role, expiresAt, emailed, link}`. When SMTP is set, `link` is always null, whether the send succeeded or failed, and `emailed` says which; the link only ever goes to the invited inbox. A caller retries a failed send with a new key. Only an instance without SMTP returns `emailed` false and `link` with the invitation link once, for the caller to deliver.
    - A repeat with the same key answers 200 with the first invitation unchanged: `emailed` as it was, `link` null, nothing revoked and nothing sent. So a caller that needs the link must keep the first answer; a hosted instance sets SMTP and never relies on `link`.
    - The same key with another email or role is 400 `invalid_request`. A new key for an email with an open invitation is 06d's resend: the open link is revoked and a new one is sent or returned.
    - Parallel creates rely on 06d's partial unique indexes (one open invitation per organisation, email and kind; one idempotency key per organisation), so exactly one open link remains.
    - An email that is already a member is 409 `already_member`. An unknown organisation is 404. A bad email or role is 400.
- **Logging:** each instance call logs its operation, organisation id and outcome, never the token, an invitation token or a link.

## Out of Scope

- Suspending, resuming or deleting an organisation, and quotas (the Cloud limits task).
- License keys and their check (0017). The `ee/` license governs use until then.
- Billing, payment providers and anything Cloud-specific. Nothing in this repository names them.
- Creating projects through this API. The workspace's admin adds them in the console or with `voicegateway project add`.
- Listing organisations.

## Constraints

- No new library: `net/http`, `crypto/sha256`, `crypto/subtle`, chi and the existing store. Invitation creation is 06d's function, not a copy.
- `ee/instance` may import `internal/` packages; nothing outside `ee/` and `internal/app` imports `ee/`.
- Every new response validates against `docs/api/openapi.yaml` in the tests (the shared validator).
- Real-thing tests: Postgres through testcontainers, `httptest` for HTTP, an SMTP sink only where delivery is checked.

## Acceptance Checks

1. Given `VOICEGATEWAY_INSTANCE_TOKEN` unset, then each of the 3 instance operations answers 404 with and without a Bearer (measured by `TestInstanceAPIOffWithoutToken`).
2. Given the token set, then each instance operation answers 401 with no credential, 403 with a session of an Admin, 403 with a project API key, and 401 with a wrong token of the same length; and the instance token answers 403 on `getWorkspaces`, `getMembers` and `listCalls` (3 × 4 + 3 = 15 cases, measured by `TestInstanceScopeIsItsOwn`).
3. Given `putOrganisation` sent 10 times in parallel for one id, then exactly 1 organisation row exists, exactly 1 answer is 201 and 9 are 200 (measured by `TestPutOrganisationIsIdempotent`).
4. Given a new organisation and no SMTP, when `createOrganisationInvitation` runs for an admin email, then the answer is 201 with `emailed` false and a link, and opening that link with 06d's accept, with a 12-character password, makes the user an Admin of that organisation and of no other; `getOrganisation` then reports `admins` 1 and `pendingInvitations` 0 (measured by `TestFirstAdminJoinsByInvitation`).
5. Given an SMTP sink, when 10 invitation requests with one `Idempotency-Key` run in parallel for one email, then 1 open invitation exists, exactly 1 email is sent, 1 answer is 201 and 9 are 200 with `link` null. The same key with another email is 400. A new key revokes the first link (refused at accept) and the new one works (measured by `TestInvitationIsNotDuplicated`).
6. Given an SMTP sink, then one create sends exactly 1 email to the invited address with the link, and the answer has `emailed` true and `link` null. Given SMTP set but failing, the answer has `emailed` false and `link` null, and a create with a new key replaces the link and sends it once the sink is back (measured by `TestInvitationEmailedWhenSMTPIsSet`).
7. Given the log output of checks 1 to 6, then no line contains the instance token, an invitation token or an invitation link (measured by `TestNoSecretInLogs`, extended).
8. Given each instance operation, then its responses in checks 2 to 6 validate against `docs/api/openapi.yaml` and its minimum role is `Instance` in both the route table and the contract (measured by the shared validator and `TestEveryRouteHasAMinimumRole`).

## Manual Check

On the Compose stack with `VOICEGATEWAY_INSTANCE_TOKEN` set and no SMTP: `curl` a `putOrganisation`, then an invitation, open the returned link in a private window, set a password, and confirm the new organisation shows in the console's switcher with no other organisation's data.
