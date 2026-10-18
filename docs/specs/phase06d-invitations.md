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
    - `idempotency_key`, nullable, unique per organisation when set;
    - `created_at`, `expires_at`, `emailed_at`, `accepted_at` and `revoked_at`.
  - A row's state is derived, never stored, so it cannot drift: `accepted` when `accepted_at` is set, `revoked` when `revoked_at` is set, `expired` when `expires_at` has passed, otherwise `pending`.
  - **One open link:** a partial unique index on `(organisation_id, lower(email), kind) WHERE accepted_at IS NULL AND revoked_at IS NULL` allows one open link per organisation, email and kind. An index cannot test `expires_at` against `now()`, so an expired, unrevoked row still counts as open.
  - **Replacing:** creating an invite revokes the open one for that email and organisation, expired or not, in the same transaction, so only the newest link works. The same holds for a reset for the same member.
  - **Parallel creates:** one insert wins the unique index. The other retries its transaction once, revoking the winner and inserting, so exactly one open link remains.
  - **Indexes:** `(organisation_id, kind, created_at)` serves the list, and `token_sha256` is unique for the lookup.
- **Tokens** use the 06c pattern:
  - 32 bytes from `crypto/rand`, base64url, stored only as SHA-256 and compared by an indexed equality on the hash.
  - The link is `<base>/invite#<token>`. The base is `VOICEGATEWAY_PUBLIC_URL` when set, otherwise the request's scheme and Host. The token sits in the fragment, so no server or proxy log ever sees it.
  - An invite expires after 7 days (0027), a reset after 24 hours.
- **The invite service**, a package of its own (`internal/invite`), used by the API handlers now and by the 06e instance API later. Its `Store` and `Mailer` interfaces live in the package, and `internal/app` wires it.

  ```go
  package invite

  // Create makes an invitation for in.Email to join in.OrganisationID as in.Role, replacing any
  // open invitation for that email there. With a Mailer set, it emails the link and never returns
  // it, whether the send succeeded or failed: only the address's owner may hold it, or anyone who
  // can invite could claim an account for an email they do not control. Without a Mailer, it
  // returns the link, the only time the full link exists. in.InvitedBy is nil for the instance API
  // and the command line.
  //
  // With in.IdempotencyKey set, a repeat call with the same key returns the invitation the first
  // call made, with Emailed as it was and Link empty (only the token's hash is kept), and revokes
  // nothing, so a retry never kills a link already emailed. The same key with another email or
  // role is ErrInvalid.
  //
  // Errors: ErrInvalid (email, role, empty BaseURL, or a reused key), ErrAlreadyMember, and
  // store.ErrNotFound for an unknown organisation.
  func (s *Service) Create(ctx context.Context, in Invite) (Created, error)

  type Invite struct {
      OrganisationID string
      Email          string
      Role           string  // "admin", "member" or "viewer"
      InvitedBy      *string // a user id, nil for the instance API and the command line
      BaseURL        string  // the link's base, from VOICEGATEWAY_PUBLIC_URL or the request; required
      IdempotencyKey string  // optional; the 06e instance API sets it so its retries are safe
  }

  type Created struct {
      Invitation store.Invitation
      Link       string // <BaseURL>/invite#<token> only without a Mailer; empty with one, and on an idempotent repeat
      Emailed    bool   // true when the send succeeded; false without SMTP, or when it failed
  }

  // ResetLink makes a password reset link for userID, replacing any open one for them. Like Create,
  // with a Mailer set it emails the link to the member and returns no link. It is
  // refused unless the requester is an admin of every organisation userID belongs to, so an admin
  // of one organisation cannot take over an account that also belongs to another. Errors:
  // store.ErrNotFound when userID is not a member of organisationID, and ErrOtherOrganisations
  // when they also belong to an organisation the requester does not administer.
  func (s *Service) ResetLink(ctx context.Context, organisationID, userID string, by *string, baseURL string) (Created, error)

  // Look reads a link's invitation by its token for the accept screen: its kind, organisation
  // name, role and email, never whether an account exists. ErrInvalidLink is the same for
  // unknown, accepted, revoked and expired.
  func (s *Service) Look(ctx context.Context, token string) (Pending, error)

  type Pending struct {
      Kind             string  // "invite" or "reset"
      OrganisationName string
      Role             *string // nil for a reset
      Email            string
  }

  // Accept uses a link once. For an invite, password either creates the account (a new email,
  // at least 12 characters) or must be the existing account's own password, checked under the
  // sign-in limits; an existing member keeps their role. For a reset, it sets the password for
  // the link's user_id and moves password_version, signing them out everywhere else. Hashing and
  // verifying run before the transaction opens; inside it, one conditional UPDATE ... RETURNING
  // uses the link, and anything but one row is ErrInvalidLink. It returns the user to sign in.
  func (s *Service) Accept(ctx context.Context, token, password string, limit SignInLimit) (store.Principal, error)
  ```
- **API** (`docs/api/openapi.yaml`, then `make api-types`). The operations are in the role table, and every response validates against the contract.
  - `getInvitations` (Admin, organisation): lists invitations newest first, each with `id`, `kind`, `email`, `role`, `state`, `createdAt`, `expiresAt` and `invitedBy` (an email, or null). `invitedBy` is null when unknown, never empty. The 06b schema gains these fields.
  - `createInvitation` `POST /api/v1/org/{orgId}/invitations`, with `{email, role}`:
    - 201 with `{invitation, link, emailed}`.
    - **With SMTP set:** `link` is always null. `emailed` is true when the send succeeded and false when it failed, and the link is never in the answer either way, so nobody who can invite can sign up as an address they do not control. To resend after a failure, call `createInvitation` again: it replaces the link and emails the new one.
    - **Without SMTP:** `link` is the full link, in this answer only, for the admin to copy.
    - 400 for a bad email or role, and 409 `already_member`.
  - `revokeInvitation` `DELETE /api/v1/org/{orgId}/invitations/{invitationId}`: 204, idempotent. An id from another organisation is 404.
  - `createPasswordReset` `POST /api/v1/org/{orgId}/members/{userId}/password-reset` (Admin):
    - 201 with `{link, expiresAt, emailed}`. The same rule as invitations: with SMTP set, the reset is emailed to the member and `link` is null; without SMTP, `link` is returned for the admin to pass on.
    - 404 when the user is not a member of the organisation.
    - 409 `member_of_other_organisations` when the user also belongs to an organisation the requester is not an admin of.
  - The four operations' minimum roles go in `internal/api/roles.go`: `getInvitations`, `createInvitation`, `revokeInvitation` and `createPasswordReset` all need Admin.
- **Accepting a link** (`internal/auth`, outside `/api/v1`, like sign-in):
  - `GET /auth/invitation` reads the token from an `X-Invitation-Token` header, never the URL. It returns `{kind, organisationName, role, email}`, or 404 `invitation_invalid`, identical for unknown, accepted, revoked and expired. It never says whether an account exists for the email: on Cloud, that would let one workspace learn about another's people.
  - **The accept flow without that answer:** the page always asks for one password, with the line "If you already have an account with this email, use its password." A new email gets an account with that password. An existing account's password must match, and a wrong one is 401, the same answer sign-in gives. Only the link's holder learns anything, and the link went to that email.
  - `POST /auth/invitation/accept`, with `{token, password}`, answers 204 and the session cookie, renewing the session id. Every rule of sign-in applies: `https_required`, cross-origin refusal, and the sign-in limits for an existing account's password.
  - **Shared limits:** accept counts its password tries in sign-in's own limiter, keyed by the invitation's email and the client address, exactly as `POST /auth/sign-in` keys them, never by token. Tries through sign-in and through accept add up, so neither path gives an attacker extra guesses.
  - **Single use:** argon2id hashing (a new password) or verifying (an existing account) runs before the transaction. Inside it, `UPDATE invitations SET accepted_at = now() WHERE token_sha256 = $1 AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING ...` uses the link, and any result but one row is 404. The membership or the new password is written in the same transaction, so a second accept, parallel or not, gets 404.
  - **A reset** writes the password for the link's `user_id`, never for whatever account the email now names, and moves `password_version`. A sign-in's #404 rehash write loses to it, since that write only replaces the hash it verified.
  - Two parallel accepts that would create the same new email: one creates the account, the other answers 404 or signs in, never 500.
  - The `/invite` page and both `/auth/invitation` endpoints send `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- **Email** over one setting, `VOICEGATEWAY_SMTP_URL`, with `VOICEGATEWAY_SMTP_FROM`:
  - The URL looks like `smtps://user:pass@host:465` or `smtp://user:pass@host:587`. The second form requires STARTTLS, and plain SMTP is refused unless the host is loopback.
  - It is sent with `github.com/wneessen/go-mail` v0.8.1, exactly pinned.
  - The message is plain text: the organisation, the role, the link and its expiry.
  - The SMTP password and the link never appear in logs. A send failure logs its class only (unreachable, auth, rejected). The invitation still stands, and the answer says `emailed: false` with the link.
  - Without the setting, nothing is sent and the console shows the link to copy. The alerts email channel will use this same setting.
- **Console** (`dashboard/`), with `VITE_MOCK_API=off`:
  - **`/invite#<token>`:** shows the organisation and role, then one password field. A reset says "Set a new password".
    - The page reads the fragment, then drops it with `history.replaceState`. It is served with `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
    - It always shows one password field, with the line about existing accounts, never a choice based on whether an account exists.
    - It shows `invitation_invalid`, `https_required`, 401 and 429 as the sign-in screen does.
  - **Settings, People:** the Invite form calls `createInvitation`.
    - With SMTP set, it says "Emailed to <email>", or "Could not send the email" with a Resend button that calls `createInvitation` again. It never shows a link.
    - Without SMTP, the link shows once in `CopyCommand`.
  - **Invitations list:** built with `src/components/data-table.tsx`. A state filter (pending, accepted, revoked, expired) and the sort are kept in the URL with `useUrlState`. Revoke asks first, through the existing Dialog.
  - **Members row:** a "Password reset" action for admins follows the same rule: it says the reset was emailed, offers Resend after a failure, or shows the link once without SMTP.
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

1. Given an admin of A and SMTP unset, when they invite `new@example.com` as Viewer, then:
   - The answer is 201 with a link of the form `<base>/invite#<43 base64url characters>` and `emailed: false`.
   - `getInvitations` lists it as `pending`, expiring 7 days from now (the test reads its own clock).
   - The stored row holds SHA-256 of the token and not the token.
   - Neither the token nor the link appears in any log line.
   (Measured by `TestCreateInvitation`.)
2. Given 10 parallel creates for the same organisation and email, then exactly one open link remains: the one stored, and every other link answers 404 (measured by `TestParallelCreatesOneOpenLink`).
3. Given `invite.Create` with an `IdempotencyKey`, when it is called again with the same key, then:
   - The second answer is the same invitation, with `Emailed` as the first call left it and an empty link.
   - The first link still works.
   - The same key with another email is `ErrInvalid`.
   - `Create` with an empty `BaseURL` is `ErrInvalid`.
   (Measured by `TestCreateIdempotentRetry`.)
4. Given that link, when the invitee accepts it with a 12-character password, then:
   - The answer is 204 with a new session id, and the session reads A as Viewer.
   - The invitation is `accepted`.
   - A second accept, and 10 parallel accepts of a fresh link, give exactly 1 success; the rest get 404 `invitation_invalid`.
   (Measured by `TestAcceptInvitationOnce`.)
5. Given an invite for an email with an account in B, when it is accepted with that account's password, then:
   - The user belongs to A and B with one account.
   - A wrong password is 401 and adds nothing.
   - Wrong tries through accept and through sign-in, mixed, count together for that email: the 11th within 15 minutes is 429 on either path.
   - An invite for someone already in A is used up, keeps their role and signs them in.
   - `GET /auth/invitation` answers the same fields for an email with an account and one without.
   (Measured by `TestAcceptInvitationExistingAccount`.)
6. Given an expired invitation (by an injected clock), a revoked one, one replaced by a newer invite, an accepted one and a random token, then `GET /auth/invitation` and accept answer 404 `invitation_invalid` with identical bodies for all 5 (measured by `TestInvitationInvalidLooksAlike`).
7. Given each of these, then the answer is as stated (measured by `TestInvitationRules`):
   - a bad email or role on `createInvitation`: 400;
   - inviting an existing member: 409 `already_member`;
   - a password under 12 characters on accept: 400;
   - a plain-HTTP accept away from localhost: 400 `https_required`;
   - an accept with an `Origin` of another site: 403, with no membership added;
   - a revoke: 204, twice;
   - an id from another organisation: 404;
   - a Viewer or Member: 403 on all four new operations.
8. Given two parallel accepts of two invites for the same new email, in two organisations, then exactly one account exists afterwards, and neither answer is 500 (measured by `TestParallelAcceptsOneAccount`).
9. Given a member signed in on two sessions and SMTP unset, when an admin makes a reset link and the member sets a new password through it, then:
   - Both old sessions answer 401, and the new password signs in.
   - `password_version` moved by 1.
   - The reset link then answers 404.
   - A reset for a user outside the admin's organisation is 404.
   (Measured by `TestPasswordResetLink`.)
10. Given a member of A who also belongs to B, when an admin of A (and not of B) asks for a reset link, then the answer is 409 `member_of_other_organisations`, and no link is stored. An admin of both A and B gets the link (measured by `TestResetRefusedAcrossOrganisations`).
11. Given `VOICEGATEWAY_SMTP_URL` pointing at a mailpit container, when an admin invites someone, then:
    - The answer says `emailed: true` with `link: null`.
    - Mailpit holds exactly 1 message to that address, whose body contains the link and the organisation name.
    - With mailpit stopped, the invitation is still created, `emailed` is false, `link` is null, and the log names the failure class, never the link or the SMTP password.
    - A second `createInvitation` (Resend) with mailpit back emails a new link, and the first one answers 404.
    - A password reset with SMTP set lands in mailpit addressed to the member, and its answer has `link: null`.
    (Measured by `TestInvitationEmail`.)
12. Given SMTP set, then `createInvitation` and `createPasswordReset` answer `link: null`, and `invite.Create` and `ResetLink` return an empty `Created.Link`, both when the send succeeds and when it fails (measured by `TestNoLinkWithSMTP`).
13. Given the instance API's call shape, when `invite.Create` runs with `InvitedBy` nil, then the invitation is stored with `invited_by` null, and `getInvitations` shows `invitedBy: null` (measured by `TestCreateWithoutInviter`).
14. Given the `/invite` page and both `/auth/invitation` endpoints, then each answer carries `Referrer-Policy: no-referrer` and `Cache-Control: no-store` (measured by `TestInvitationHeaders`).
15. Given the router, then each of the four new `/api/v1` operations has its minimum role (Admin) in `routeAccess`, matching the contract's `x-access` (measured by `TestEveryRouteHasAMinimumRole`).
16. Given each new operation, then every response in checks 1 to 13 validates against the contract (measured by the response validator in those tests).
17. Given the console with the mock off, the Vitest suites `invite-page.test.tsx` and `invitations.test.tsx` run 7 cases (measured by those suites):
    - the accept screen shows the organisation and role, and one password field, and opens the console on 204;
    - it drops the fragment from the address bar;
    - it shows "This invitation is no longer valid" for `invitation_invalid`;
    - an invite with SMTP unset shows the link once, and with SMTP set shows no link and, after a failed send, a Resend button;
    - the list filters by state through the URL;
    - Revoke asks first;
    - a Viewer sees no Invite form and no reset action.
18. Given the log output of checks 1 to 13, then no line contains a token, a link, a password, a session id or the SMTP password (measured by `TestNoSecretInLogs`, extended).

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
- **Idempotency:** an optional `IdempotencyKey` on `Invite`, per organisation, so the 06e instance API's retries return the first invitation instead of revoking an emailed link. Without a key, a repeat create replaces the open link.
- **No account hint on the accept page:** one password field for everyone, and the same answer from `GET /auth/invitation` whether or not an account exists.
- **Size:** this is one phase's work, in line with 06a. If the build runs long, email (checks 11 and 12) can move to a follow-up, with links copied by hand meanwhile.
