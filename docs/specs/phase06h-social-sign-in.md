# Phase 06h: Google and GitHub sign-in

## Goal

A person with an account, or with a pending invitation, can sign in to the console with Google or GitHub instead of a password, on any install where an operator has set that provider's client id and secret. An install with nothing set behaves exactly as it does today.

## Context

- Decision 0035: Google and GitHub are OSS, off unless configured, never open sign-up, and reach an account only through a verified email. Identities are stored by provider subject.
- Decision 0027: built-in accounts, scs sessions in Postgres, `CrossOriginProtection`, the `Secure` cookie rule, and the CLAUDE.md rules (never log a session id or token, constant-time credential checks).
- Decision 0028: an invitation reaches only the paid address; the first admin of a Cloud workspace arrives by invitation.
- Decision 0017: SAML, any-issuer OIDC, enforced SSO and SCIM stay Enterprise.
- `docs/specs/phase06a-sign-in.md`: sign-in limits, the session renewal on sign-in, `https_required`.
- `docs/specs/phase06d-invitations.md`: `invite.Service.Look` and `Accept`, the single-use `UPDATE ... RETURNING`, and the `/invite#<token>` page.
- `dashboard/src/features/auth/sign-in-screen.tsx` and `invite-page.tsx`.

## In Scope

- **Configuration** (`internal/config`), each pair all-or-nothing, refused at start when half set:
  - `VOICEGATEWAY_GOOGLE_CLIENT_ID` and `VOICEGATEWAY_GOOGLE_CLIENT_SECRET`.
  - `VOICEGATEWAY_GITHUB_CLIENT_ID` and `VOICEGATEWAY_GITHUB_CLIENT_SECRET`.
  - Either provider requires `VOICEGATEWAY_PUBLIC_URL` with `https`, or `http://localhost`, since the callback must be an exact registered URL. Start fails with a message naming the missing setting.
  - Callbacks are `<PUBLIC_URL>/auth/oauth/google/callback` and `<PUBLIC_URL>/auth/oauth/github/callback`. `voicegateway preflight` prints them so an operator can paste them into the provider's console.
  - Secrets are never logged, and preflight prints only whether each provider is set.
- **Postgres** (migration `00022_identities.sql`, sqlc):
  - Table `identities`: `provider` (`google` or `github`), `subject` (the provider's stable user id), `user_id` (references `users`, cascade on delete), `email` at link time, `created_at`, `last_used_at`. Primary key `(provider, subject)`; index on `user_id`.
  - `users.password_hash` becomes nullable. Null means the account has no password. Password sign-in against a null hash runs a dummy argon2id verify and answers the same 401 as a wrong password, so timing does not reveal it.
- **Providers** in a new package `internal/oauth`, used by `internal/auth`, wired in `internal/app`:
  - `golang.org/x/oauth2` (promoted from indirect to a direct requirement at the version already in go.sum) with its `google` and `github` endpoints, authorization code flow with PKCE (`oauth2.GenerateVerifier`, `S256ChallengeOption`).
  - Google: scopes `openid email`, ID token verified with `github.com/coreos/go-oidc/v3` v3.21.0 (exact pin) against `https://accounts.google.com`, audience the client id. The email is used only when `email_verified` is true. The subject is the token's `sub`.
  - GitHub: scope `user:email`. The subject is the numeric `id` from `GET /user`. The email is the entry of `GET /user/emails` with `primary: true` and `verified: true`; none is a refusal.
  - Provider endpoints are fields on the provider config, so tests point them at an `httptest` server.
- **Routes** (`internal/auth`, outside `/api/v1`, like sign-in):
  - `GET /auth/providers` answers `{"google": bool, "github": bool}`. Unauthenticated, cacheable for 60 s.
  - `GET /auth/oauth/{provider}/start` with optional `?invitation=1`: stores `state`, the PKCE verifier, the provider and, for an invitation, the invitation token's SHA-256, in the scs session (no new cookie), then redirects to the provider. The invitation token reaches this route in an `X-Invitation-Token` header from the console's fetch (06d's rule: never in a URL), which answers the redirect URL as JSON for the console to follow. An unconfigured provider is 404.
  - `GET /auth/oauth/{provider}/callback`: checks `state` in constant time against the session copy and drops it (single use), exchanges the code with the verifier, reads subject and verified email, then decides:
    1. An identity `(provider, subject)` exists: sign in its user.
    2. Else a user has that email: link the identity, then sign in.
    3. Else, with an invitation hash in the session: run 06d's `Accept` with the provider's verified email in place of a password. It succeeds only when the invitation's email equals the verified email; it creates the user with a null password hash, links the identity, and is single use like any accept.
    4. Else: refuse.
  - Every refusal (no verified email, no account, invitation email mismatch, used or expired invitation, bad state) redirects to `/sign-in?error=oauth_refused` with no detail, the same for all causes, so the console reveals nothing about who has an account. The cause is logged as a class (`no_verified_email`, `no_account`, `invitation_mismatch`, `state`), never with the email.
  - Success renews the session id exactly as password sign-in does, sets the same session keys, writes `last_used_at`, and redirects to `/` (or to the organisation the invitation joined).
  - A password reset (06d) on an account with a null hash sets its first password; nothing else changes for resets.
  - The sign-in rate limit counts each callback against the client address bucket.
- **Console** (`dashboard/`):
  - The sign-in screen and the `/invite` page call `/auth/providers` and show "Continue with Google" and "Continue with GitHub" buttons only for providers that are on, above the email form with an "or" divider. Icons are Phosphor's `GoogleLogo` and `GithubLogo`. The layout comes from a shadcn login block through the shadcn CLI, not hand-written markup.
  - `?error=oauth_refused` shows one line: "We could not sign you in with that account. Use the email you were invited with." The parameter is dropped with `history.replaceState`.
  - Settings, Account lists the linked providers (read only) and says "No password set" for a null hash, with no other way to set one in this phase than a reset.
- **API contract** (`docs/api/openapi.yaml`, then `make api-types`): `getAuthProviders`, and the account profile gains `identities: [{provider, linkedAt}]` and `hasPassword`.

## Out of Scope

- Any provider but Google and GitHub, and any-issuer OIDC, SAML, SCIM and enforced SSO (Enterprise, 0017).
- Open sign-up through a provider. An unknown email is always refused.
- Unlinking an identity or setting a password from Settings without a reset.
- The control plane (`voicegateway-cloud`). Creating the two OAuth apps and setting their secrets on the Cloud host is an operations step after this merges.
- The rest of the login page redesign beyond the two buttons and the divider.

## Constraints

- Libraries: `golang.org/x/oauth2` and `github.com/coreos/go-oidc/v3` v3.21.0, exact pins. No goth, no auth proxy, no provider SDK. Tests sign ID tokens with `github.com/go-jose/go-jose/v4`, already in the module graph through go-oidc.
- No new cookie. `state` and the verifier live in the scs session.
- Never log a code, a token, a state, an email or a client secret.
- Every redirect target is a fixed path on the console's own origin. No `next` or `redirect_uri` parameter is accepted from the browser.

## Acceptance Checks

1. Given no provider settings, when web starts, then `/auth/providers` answers `{"google":false,"github":false}`, both `/auth/oauth/*/start` routes answer 404, and the 06a sign-in tests pass unchanged (measured by `go test -race ./internal/auth/...` and `config_test.go`).
2. Given only `VOICEGATEWAY_GOOGLE_CLIENT_ID` set, when web starts, then it exits non-zero naming `VOICEGATEWAY_GOOGLE_CLIENT_SECRET`; the same for the other three half-set cases, 4 of 4 (measured by a table test in `internal/config`).
3. Given a test Google issuer and an existing user with that email, when the callback arrives with a valid code, then the user is signed in, one `identities` row exists, and a second sign-in after the issuer changes the email still signs in the same user by subject (measured by `internal/oauth` and `internal/auth` tests against `httptest` and Postgres in testcontainers).
4. Given an ID token with `email_verified: false`, or GitHub emails with no verified primary, when the callback arrives, then the answer is the `oauth_refused` redirect, no identity is written and no session is set, 2 of 2 (measured by those tests).
5. Given an install with users and a verified email that matches no account and no invitation, when the callback arrives, then the redirect is byte-identical to case 4's and the users table row count is unchanged (measured by those tests).
6. Given a pending invitation for a@example.com, when the callback carries a verified a@example.com, then exactly one user with a null password hash exists, the membership has the invited role, the invitation is accepted, and replaying the same invitation gets `oauth_refused`. With a verified b@example.com instead, nothing is written (measured by tests against Postgres, 10 parallel callbacks for the same invitation giving 1 user and 1 accepted invitation).
7. Given a callback with a `state` that differs from the session's by one character, or a replayed `state`, then the answer is the `oauth_refused` redirect and the code is never exchanged (measured by the test's token endpoint seeing 0 requests).
8. Given a user with a null password hash, when they try password sign-in, then the answer is the same 401 as a wrong password, and the median response time over 50 tries is within 20% of a wrong password's (measured by an `internal/auth` test).
9. Given both providers on, when the sign-in page loads, then both buttons render above the email form, and with only GitHub on, only that button renders (measured by Vitest with Testing Library, 2 cases).
10. Given the full flow against the test issuer, when a person clicks "Continue with Google" from an `/invite` link, then they land in the invited organisation signed in (measured by a Playwright test in `make e2e`).

## Manual Check

On a staging host with real Google and GitHub OAuth apps: accept an invitation with Google, sign out, sign in with GitHub on the same email, and confirm Settings shows both providers linked and "No password set".

## Open Questions

- None.
