# Phase 06d: Invitations and password reset links

## Goal

An organisation admin invites someone by email with a role. The invitee gets the link by email when SMTP is set, or the admin copies it from the console. The invitee opens the link, sets a password (or signs in, if they already have an account) and joins the organisation. An admin can also make a password reset link for a member, the reset path for anyone without a shell, Cloud's organisation admins among them. This is the fourth phase for decision 0027, after 06a (sign-in), 06b (members and roles) and 06c (project API keys). With it, the plan task "Wire teams and projects" is done.

## Context

- Decision 0027, "Invitations": a single-use link with a random token, stored hashed, expiring in 7 days, scoped to one organisation and role. Web emails it when SMTP is set; otherwise the admin copies it from Settings. Without SMTP, an admin resets a member's password. Its CLAUDE.md rules apply: never log a password, a session id or an invitation token.
- Decision 0019: Admin, Member and Viewer, and "Invite people by email" in OSS.
- Decision 0024: Cloud runs the public image; its control plane will invite a workspace's first admin through an instance API. That API is phase 06e. Its decision is not on main yet (the brief names it 0028). This phase only provides the invite function it will call.
- `docs/specs/phase06b-members-and-roles.md`: the role table, `getMembers`, `user add --role` and `user reset-password` (`password_version` signs other sessions out).
- `docs/specs/phase06c-project-keys.md`: the pattern for a random secret shown once and stored as SHA-256.
- `docs/progress.md`, "Carried to the 06d spec": the review points from 06b, all taken here.
- `docs/api/openapi.yaml`: `getInvitations` (Admin, organisation scope) and the `Invitation` schema (`email`, `role`).
- `dashboard/src/features/settings/settings-dialog.tsx`: the People page's Invite form, which today keeps invites in the browser (`vg.invites`).

## In Scope

- **Postgres** (migration `00009_invitations.sql`, the next free number after `00008_query_indexes`; sqlc):
  - Table `invitations`, with these columns:
    - `id` uuid;
    - `kind`, `invite` or `reset`;
    - `organisation_id`, the organisation an invite is for, or the one whose admin made a reset;
    - `email`, case-folded;
    - `role`, null for a reset;
    - `user_id`, the member a reset is for, null for an invite;
    - `token_sha256`, unique;
    - `invited_by`, nullable for the instance API and the command line;
    - `created_at`, `expires_at`, `accepted_at` and `revoked_at`.
  - A row's state is derived, never stored, so it cannot drift: `accepted` when `accepted_at` is set, `revoked` when `revoked_at` is set, `expired` when `expires_at` has passed, otherwise `pending`.
  - Creating an invite for an email that has a pending one in the same organisation revokes the old one in the same transaction, so only the newest link works. The same holds for a reset for the same member.
  - Index on `(organisation_id, kind, created_at)` for the list. `token_sha256` is unique for the lookup.
- **Tokens** use the 06c pattern:
  - 32 bytes from `crypto/rand`, base64url, stored only as SHA-256 and compared by an indexed equality on the hash.
  - The link is `<base>/invite#<token>`. The base is `VOICEGATEWAY_PUBLIC_URL` when set, otherwise the request's scheme and Host. The token sits in the fragment, so no server or proxy log ever sees it.
  - An invite expires after 7 days (0027), a reset after 24 hours.
- **The invite service**, a package of its own (`internal/invite`), used by the API handlers now and by the 06e instance API later. Its `Store` and `Mailer` interfaces live in the package, and `internal/app` wires it.

  ```go
  package invite

  // Create makes an invitation for in.Email to join in.OrganisationID as in.Role, replacing any
  // pending invitation for that email there. It emails the link when a Mailer is set and returns
  // the link either way: the only time the full link exists. in.InvitedBy is nil for the instance
  // API and the command line. Errors: ErrInvalid (email or role), ErrAlreadyMember, and
  // store.ErrNotFound for an unknown organisation.
  func (s *Service) Create(ctx context.Context, in Invite) (Created, error)

  type Invite struct {
      OrganisationID string
      Email          string
      Role           string  // "admin", "member" or "viewer"
      InvitedBy      *string // a user id, nil for the instance API and the command line
      BaseURL        string  // the link's base, from VOICEGATEWAY_PUBLIC_URL or the request
  }

  type Created struct {
      Invitation store.Invitation
      Link       string // <BaseURL>/invite#<token>
      Emailed    bool   // false without SMTP, or when the send failed
  }

  // ResetLink makes a password reset link for a member of organisationID, replacing any pending
  // one for them. Errors: store.ErrNotFound when userID is not a member there.
  func (s *Service) ResetLink(ctx context.Context, organisationID, userID string, by *string, baseURL string) (Created, error)

  // Look reads a link's invitation by its token for the accept screen: ErrInvalidLink, the same
  // for unknown, accepted, revoked and expired.
  func (s *Service) Look(ctx context.Context, token string) (Pending, error)

  // Accept uses a link once. For an invite: a new email gets an account with password (at least
  // 12 characters); an existing account must give its own password, through the same sign-in
  // limits; an existing member keeps their role. For a reset: it sets the password and moves
  // password_version, signing the member out everywhere else. It returns the user to sign in.
  func (s *Service) Accept(ctx context.Context, token, password string, limit SignInLimit) (store.Principal, error)
  ```
- **API** (`docs/api/openapi.yaml`, then `make api-types`). The operations are in the role table, and every response validates against the contract.
  - `getInvitations` (Admin, organisation): lists invitations newest first, each with `id`, `kind`, `email`, `role`, `state`, `createdAt`, `expiresAt` and `invitedBy` (an email, or null). `invitedBy` is null when unknown, never empty. The 06b schema gains these fields.
  - `createInvitation` `POST /api/v1/org/{orgId}/invitations`, with `{email, role}`:
    - 201 with `{invitation, link, emailed}`. The link is in this answer only.
    - 400 for a bad email or role, and 409 `already_member`.
  - `revokeInvitation` `DELETE /api/v1/org/{orgId}/invitations/{invitationId}`: 204, idempotent. An id from another organisation is 404.
  - `createPasswordReset` `POST /api/v1/org/{orgId}/members/{userId}/password-reset` (Admin):
    - 201 with `{link, expiresAt, emailed}`.
    - 404 when the user is not a member of the organisation.
- **Accepting a link** (`internal/auth`, outside `/api/v1`, like sign-in):
  - `GET /auth/invitation` reads the token from an `X-Invitation-Token` header, never the URL. It returns `{kind, organisationName, role, email, hasAccount}`, or 404 `invitation_invalid`, identical for unknown, accepted, revoked and expired.
  - `POST /auth/invitation/accept`, with `{token, password}`, answers 204 and the session cookie, renewing the session id. Every rule of sign-in applies: `https_required`, cross-origin refusal, and the sign-in limits for an existing account's password.
  - The token is used in the same transaction that adds the membership or sets the password, through a conditional update (`WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`), so a second accept, parallel or not, gets 404.
  - Two parallel accepts that would create the same new email: one creates the account, the other answers 404 or signs in, never 500.
- **Email** over one setting, `VOICEGATEWAY_SMTP_URL`, with `VOICEGATEWAY_SMTP_FROM`:
  - The URL looks like `smtps://user:pass@host:465` or `smtp://user:pass@host:587`. The second form requires STARTTLS, and plain SMTP is refused unless the host is loopback.
  - It is sent with `github.com/wneessen/go-mail` v0.8.1, exactly pinned.
  - The message is plain text: the organisation, the role, the link and its expiry.
  - The SMTP password and the link never appear in logs. A send failure logs its class only (unreachable, auth, rejected). The invitation still stands, and the answer says `emailed: false` with the link.
  - Without the setting, nothing is sent and the console shows the link to copy. The alerts email channel will use this same setting.
- **Console** (`dashboard/`), with `VITE_MOCK_API=off`:
  - **`/invite#<token>`:** shows the organisation and role, then "Set a password", or "Sign in to join" when `hasAccount`. A reset says "Set a new password".
    - The page reads the fragment, then drops it with `history.replaceState`, and is served with `Referrer-Policy: no-referrer`.
    - It shows `invitation_invalid`, `https_required`, 401 and 429 as the sign-in screen does.
  - **Settings, People:** the Invite form calls `createInvitation`. The link shows once in `CopyCommand`, with "Emailed to <email>" when sent.
  - **Invitations list:** built with `src/components/data-table.tsx`. A state filter (pending, accepted, revoked, expired) and the sort are kept in the URL with `useUrlState`. Revoke asks first, through the existing Dialog.
  - **Members row:** a "Password reset link" action for admins shows its link once in the same way.
  - The browser-kept `vg.invites` is removed. The mock serves the new operations in memory.

## Out of Scope

- The instance API itself, `/api/v1/instance` (06e), which will call `invite.Create` to invite a first admin.
- Self-service "forgot password" from the sign-in screen. Resets are made by an admin, or with `user reset-password`.
- HTML email, templates per organisation, bounce handling, and per-organisation SMTP.
- Roles per project (Enterprise, 0019), SSO, SAML and SCIM (0017).

## Constraints

- **Library first:**
  - `wneessen/go-mail` for SMTP: maintained, with STARTTLS, implicit TLS and auth.
  - `crypto/rand`, `crypto/sha256` and `crypto/subtle` for tokens, as in 06c.
  - The existing scs sessions, argon2id hashing and sign-in limits for accept.
  - Hand-written: only the invitation state machine (a few SQL conditions). No library models it, and it is the product's own rule.
- **CLAUDE.md and decision 0027:**
  - Never log a token, a link, a password, a session id or an email body. A refused or expired token logs its invitation id only, when known.
  - Every `/api/v1` route goes through the gate and the role table.
  - Compare in constant time where a comparison is not a hash lookup.
- Unknown values stay null (`invitedBy`, and `role` on a reset).
- **The console** uses shadcn and ReUI components, `data-table.tsx`, `CopyCommand`, the existing Dialog, and URL state for the list's filter and sort.
- **Real-thing tests:**
  - Postgres through testcontainers.
  - SMTP through a mailpit container (`axllent/mailpit`, pinned by tag and digest, through testcontainers' generic container), whose HTTP API the test reads.
  - `httptest`, and Vitest with Testing Library.

## Acceptance Checks

1. Given an admin of A, when they invite `new@example.com` as Viewer with SMTP unset, then:
   - The answer is 201 with a link of the form `<base>/invite#<43 base64url characters>` and `emailed: false`.
   - `getInvitations` lists it as `pending`, expiring 7 days from now (the test reads its own clock).
   - The stored row holds SHA-256 of the token and not the token.
   - Neither the token nor the link appears in any log line.
   (Measured by `TestCreateInvitation`.)
2. Given that link, when the invitee accepts it with a 12-character password, then:
   - The answer is 204 with a new session id, and the session reads A as Viewer.
   - The invitation is `accepted`.
   - A second accept, and 10 parallel accepts of a fresh link, give exactly 1 success; the rest get 404 `invitation_invalid`.
   (Measured by `TestAcceptInvitationOnce`.)
3. Given an invite for an email with an account in B, when it is accepted with that account's password, then the user belongs to A and B with one account. A wrong password is 401 and adds nothing. The 11th wrong try in 15 minutes is 429. An invite for someone already in A is used up, keeps their role and signs them in (measured by `TestAcceptInvitationExistingAccount`).
4. Given an expired invitation (by an injected clock), a revoked one, one replaced by a newer invite, an accepted one and a random token, then `GET /auth/invitation` and accept answer 404 `invitation_invalid` with identical bodies for all 5 (measured by `TestInvitationInvalidLooksAlike`).
5. Given a bad email or role, then `createInvitation` is 400. Inviting an existing member is 409 `already_member`. A password under 12 characters on accept is 400, and a plain-HTTP accept away from localhost is 400 `https_required`. A revoke is 204 twice, and an id from another organisation is 404. A Viewer or Member gets 403 on all four new operations (measured by `TestInvitationRules`).
6. Given two parallel accepts of two invites for the same new email, in two organisations, then exactly one account exists afterwards, and neither answer is 500 (measured by `TestParallelAcceptsOneAccount`).
7. Given a member signed in on two sessions, when an admin makes a reset link and the member sets a new password through it, then:
   - Both old sessions answer 401, and the new password signs in.
   - `password_version` moved by 1.
   - The reset link then answers 404.
   - A reset for a user outside the admin's organisation is 404.
   (Measured by `TestPasswordResetLink`.)
8. Given `VOICEGATEWAY_SMTP_URL` pointing at a mailpit container, when an admin invites someone, then:
   - The answer says `emailed: true`.
   - Mailpit holds exactly 1 message to that address, whose body contains the link and the organisation name.
   - With mailpit stopped, the invitation is still created, `emailed` is false, and the log names the failure class, never the link or the SMTP password.
   (Measured by `TestInvitationEmail`.)
9. Given the instance API's call shape, when `invite.Create` runs with `InvitedBy` nil, then the invitation is stored with `invited_by` null, and `getInvitations` shows `invitedBy: null` (measured by `TestCreateWithoutInviter`).
10. Given each new operation, then every response in checks 1 to 9 validates against the contract (measured by the response validator in those tests).
11. Given the console with the mock off, the Vitest suites `invite-page.test.tsx` and `invitations.test.tsx` run 7 cases (measured by those suites):
    - the accept screen shows the organisation and role, and opens the console on 204;
    - it drops the fragment from the address bar;
    - it shows "This invitation is no longer valid" for `invitation_invalid`;
    - an invite with SMTP unset shows the link once;
    - the list filters by state through the URL;
    - Revoke asks first;
    - a Viewer sees no Invite form and no reset action.
12. Given the log output of checks 1 to 9, then no line contains a token, a link, a password, a session id or the SMTP password (measured by `TestNoSecretInLogs`, extended).

## Manual Check

On the Compose stack behind HTTPS, with mailpit as SMTP:
1. Invite a second address as Member, open the email's link in a private window, set a password, and confirm the Member sees calls.
2. Make a reset link for that member, use it, and confirm the first window is signed out.
3. Unset SMTP, invite again, and copy the link from the console.

## Open Questions

These are my recommendations; change any before building.

- **Reset links expire after 24 hours,** where invites take 7 days. A reset is a credential and should not live as long as an invite.
- **The service sits in `internal/invite`,** a package of its own, rather than in `internal/auth`. That way the 06e instance API depends on it without depending on sign-in.
- **One setting for SMTP,** `VOICEGATEWAY_SMTP_URL`, rather than host, port, user and password set separately. One value is easier to keep secret, and go-mail takes its parts. The alerts email channel reuses it.
- **The spec's file name** is `phase06d-invitations.md`, matching the other 06 specs, rather than `06d-invitations.md`.
- **Size:** this is one phase's work, in line with 06a. If the build runs long, email (check 8) can move to a follow-up, with links copied by hand meanwhile.
