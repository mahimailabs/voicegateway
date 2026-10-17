# Phase 06a: Sign in with built-in accounts

## Goal

A person with an account signs in to the console with their email and password, and every `/api/v1` read then works with their session, scoped to the organisations they belong to. The first admin is created from the command line. This is the first of three phases for decision 0027 and the plan task "Wire teams and projects": 06b adds invitations, members and roles, and 06c adds project API keys and retires the interim `VOICEGATEWAY_API_TOKEN`.

## Context

- Decision 0027: built-in accounts, server-side sessions, the HTTPS cookie rule, the install steps beyond `docker compose up`, and the rules it added to CLAUDE.md.
- Decision 0019: organisation roles. Every route served today is a read, which every role may make, so roles are stored here and enforced from 06b.
- Decision 0011: web serves the console from the same origin.
- `docs/api/openapi.yaml`: `getWorkspaces` (line 866) and every operation's access text: no valid authentication is 401, denied membership is 403, cross-project ids are `not_found`.
- `docs/specs/phase04c-get-call.md`: the `/api/v1` router and its gate, which this phase extends.
- DESIGN.md ("The Clean Room"): the sign-in screen is composed from shadcn components with its tokens.

## In Scope

- **Postgres** (migration, sqlc):
  - `users`: id, email (unique, case-folded), argon2id password hash, created at.
  - `memberships`: user, organisation, role `admin | member | viewer`.
  - The `sessions` table `scs/pgxstore` reads, created by our own goose migration.
  - `sign_in_attempts`: a counted window per email and per client address.
- **`voicegateway user add --admin --email <e> --org <o>`**: creates a user and an admin membership. The password is read from `VOICEGATEWAY_USER_PASSWORD` or a prompt, never a flag, as `project add` reads its secret. It refuses a second user with the same email.
- **`internal/auth`**, mounted by web outside `/api/v1`:
  - `POST /auth/sign-in` with `{email, password}` answers 204 and sets the session cookie.
  - `POST /auth/sign-out` deletes the session server-side.
  - `GET /auth/session` returns `{email, organisations: [{id, role}]}` for the signed-in user, or 401.
  - These are not console reads, so they are not in `docs/api/openapi.yaml`.
- **Passwords:** argon2id (`golang.org/x/crypto/argon2`) with t=3, m=64 MiB, p=4, a 16-byte random salt and a 32-byte key, stored as a PHC string (`$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`) and compared with `subtle.ConstantTimeCompare`. A semaphore caps concurrent hashes at the number of CPUs, so a burst of sign-ins queues instead of exhausting memory. An unknown email and a wrong password get the same 401 and take the same time, with a dummy hash compared for an unknown email.
- **The client address**, for the per-address limit: the connection's peer, unless the peer is in `VOICEGATEWAY_TRUSTED_PROXIES` (a list of addresses and CIDRs). Then it is the right-most hop of `X-Forwarded-For` that is not itself a trusted proxy. A forwarded header from an untrusted peer is ignored, so it cannot choose its own address.
- **Sign-in limits:** after 10 attempts for one email, or 50 from one client address (an IPv6 address by its /64), in 15 minutes, sign-in answers 429 with `Retry-After` until the attempt that keeps it at the limit leaves the window.
  - Each attempt is reserved before its password is checked, in one transaction under per-key advisory locks: count, refuse at the limit, record. A parallel burst cannot pass the limit together.
  - A successful sign-in gives its reservation back.
  - The counts live in Postgres, so several web replicas share them.
  - A loopback peer that is not a trusted proxy but forwards for someone (a reverse proxy on the same host left out of `VOICEGATEWAY_TRUSTED_PROXIES`) is warned about once, since it makes every client share one address. Known trade-off: anyone who knows an email can lock that account for 15 minutes by failing on purpose. The per-email limit is kept anyway, because without it one address could guess one password without end.
- **Sessions** use `alexedwards/scs/v2` with `pgxstore`:
  - The cookie is HttpOnly and SameSite=Lax, with a 12-hour idle and 7-day absolute lifetime. The session id is renewed on sign-in.
  - It is `Secure` when `VOICEGATEWAY_PUBLIC_URL` is `https`, or when a request from an address in `VOICEGATEWAY_TRUSTED_PROXIES` carries `X-Forwarded-Proto: https`.
  - `VOICEGATEWAY_PUBLIC_URL` only sets the cookie's Secure flag; it never lifts the check below.
  - A password over plain HTTP is accepted only when the request is addressed to `localhost` (its `Host` header names localhost or a loopback address). The peer address cannot decide this, because behind Docker's port forwarding every local request arrives from a bridge address. A client that lies about `Host` exposes only its own password. Anything else answers 400 with `https_required` and stores nothing, so the console can say HTTPS is required instead of looping.
- **Cross-origin writes:** `net/http`'s `CrossOriginProtection` wraps web's whole mux, so a write from another origin is 403 on every route, not only `/auth`.
- **The `/api/v1` gate** accepts either a valid session or, until 06c, the interim bearer token.
  - With a session, it checks that the user is a member of the organisation in the path. A non-member gets 403 `forbidden`.
  - A request with neither credential is 401 as today.
- **`getWorkspaces`**, served from storage: the signed-in user's organisations and their projects. A user with no membership gets an empty list. It needs a session, not the bearer token, which names no user.
- **Console** (`dashboard/`):
  - With `VITE_MOCK_API=off`, the console asks `GET /auth/session` on load. On 401 it shows a sign-in screen composed from shadcn components.
  - The screen handles four answers:
    - `https_required`: it says HTTPS is required.
    - 401: "Email or password is wrong".
    - 429: when to try again.
    - 204: it opens the console.
  - With no users in the install, the screen shows the exact `docker compose exec web voicegateway user add --admin ...` command. `GET /auth/session` signals this with `{needsAdmin: true}` in its 401 body, and the body says nothing more.
  - The account menu's sign-out calls `POST /auth/sign-out`.
  - The Vite dev server proxies `/api` and `/auth` to web, so development is same-origin too.
  - With the mock on, nothing changes.

## Out of Scope

- Invitations, the Members page, changing roles, and role enforcement on writes (06b).
- Project API keys, MCP authentication, and removing `VOICEGATEWAY_API_TOKEN` (06c).
- Password reset by email, SMTP, two-factor sign-in, SSO, SAML and SCIM (Enterprise, 0017).
- Switching the console's default from the mock to the server: most pages are not served yet.

## Constraints

- Library first: `scs/v2` v2.9.0 and its `pgxstore` pinned at `v0.0.0-20251002162104-209de6e426de`, argon2 from `golang.org/x/crypto` (already in go.mod), and `CrossOriginProtection` from the standard library. No hand-written session or CSRF code.
- CLAUDE.md, decision 0027:
  - Never log a password, a session id, an invitation token or an API key secret.
  - Every `/api/v1` route goes through the one gate.
  - Credentials are compared in constant time.
- The console uses shadcn components and the DESIGN.md tokens; no hand-written component markup.
- Real-thing tests: Postgres through testcontainers, `httptest` for the HTTP flows, Vitest with Testing Library for the screen.

## Acceptance Checks

1. Given an install with no users, when `voicegateway user add --admin` runs with a password from the environment, then 1 user and 1 admin membership exist. The stored hash is argon2id and does not contain the password. A second run with the same email fails and adds nothing (measured by `TestUserAddCreatesFirstAdmin`).
2. Given that user, when `POST /auth/sign-in` sends the right password over HTTPS, then it answers 204 with a cookie that is HttpOnly, SameSite=Lax and Secure. `GET /auth/session` then returns the email and its admin organisation. After `POST /auth/sign-out`, the same cookie gets 401 (measured by `TestSignInSessionSignOut`).
3. Given a wrong password and an unknown email, then both answer 401 with the same body. Over 20 tries each, their median times differ by less than 25% (measured by `TestSignInFailuresLookAlike`).
4. Given 10 failed sign-ins for one email within 15 minutes, then the 11th answers 429 with a `Retry-After` header, even with the right password. Each sign-in in this check goes to a different web instance over the same Postgres (measured by `TestSignInLimitIsShared`).
5. Given a plain-HTTP request from `10.0.0.5`, then sign-in answers 400 `https_required` and sets no cookie. Addressed to `localhost` over plain HTTP, it answers 204 with a cookie that is not `Secure`, also when the peer is a Docker bridge address. Through a trusted proxy sending `X-Forwarded-Proto: https`, the cookie is `Secure`. From an untrusted address sending the same header, it is treated as plain HTTP (measured by `TestSessionCookieFollowsHTTPS`).
5a. Given web behind a trusted proxy, when two clients at `203.0.113.7` and `203.0.113.8` each fail 50 times through it, then each is limited on its own address and neither locks out the other. A request whose peer is not trusted, carrying `X-Forwarded-For: 203.0.113.7`, is counted under its peer's address, not the header's (measured by `TestClientAddressBehindProxy`).
6. Given a sign-in `POST` with an `Origin` of another site, then it answers 403 and sets no cookie (measured by `TestCrossOriginSignInRefused`).
7. Given a signed-in member of organisation A, when they read `getCall` for a call in A, then 200. For a call in organisation B, where they are not a member, 403 `forbidden`. With no cookie and no token, 401. With the interim token, 200 as before (measured by `TestGateAcceptsSessionScopedToMembership`).
8. Given a user in organisation A with projects p1 and p2, and an organisation B they do not belong to, then `getWorkspaces` returns A with p1 and p2 and nothing of B. A user with no membership gets an empty list. With only the bearer token, it is 401. Every response validates against the contract (measured by `TestGetWorkspacesFromStorage`).
9. Given the console with the mock off, when `/auth/session` answers 401, then the sign-in screen shows. Signing in with 204 opens the console. A 401 shows "Email or password is wrong", 429 shows when to try again, `https_required` shows that HTTPS is required, and `{needsAdmin: true}` shows the `user add` command (measured by the Vitest suite `sign-in.test.tsx`, 5 cases).
10. Given the log output of checks 1 to 8, then no line contains a password, a session id or the token (measured by `TestNoSecretInLogs`).
11. Given 30 parallel sign-ins for one email from 30 addresses, each with a wrong password, then exactly 10 are checked (401) and 20 refused (429). Without the per-key lock more get through (measured by `TestParallelSignInsCannotPassTheLimit`).
12. Given an https public URL and a plain-HTTP request addressed to `10.0.0.5`, then sign-in answers 400 `https_required` (measured by `TestPublicHTTPSDoesNotLiftHTTPSRequired`).
13. Given 50 failures from addresses in one IPv6 /64, then a 51st from another address in it is 429. Twelve successful sign-ins in a row each answer 204 (measured by `TestAddressLimitsByIPv6PrefixAndSuccessesAreFree`).

## Manual Check

On the stack behind a local HTTPS proxy:
1. Run `docker compose exec web voicegateway user add --admin`.
2. Open the console with `VITE_MOCK_API=off`, sign in, and open the two stored calls.
3. Sign out, and confirm the API answers 401.
4. Over plain HTTP by IP, confirm the screen says HTTPS is required.

## Open Questions

- The session lifetimes (12 hours idle, 7 days absolute) and the sign-in limits (10 per email, 50 per address, 15 minutes) are my recommendations. Change them before building if you want others.
