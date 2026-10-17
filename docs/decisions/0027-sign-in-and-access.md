# 0027: Built-in accounts, sessions and project keys for sign-in and access

- Status: proposed
- Date: 2026-10-17

## Context

The plan task "Wire teams and projects" is done when an invited user can join the intended organisation and use only the projects and actions their role permits. Today `/api/v1` sits behind one shared bearer token (phase 04c), an interim gate with no users and no roles. The contract already fixes the rules: a missing credential is 401, denied membership is 403, an id from another project is `not_found`, and server-side LiveKit credentials never reach the browser. It leaves the browser session transport unchosen.

The constraints that make this a decision:

- OSS sign-in needs no outside service. ARCHITECTURE.md says nothing leaves the user's servers, and `docker compose up` must be the whole install (principle 1, decision 0005).
- 0019 puts invites and the organisation roles Admin, Member and Viewer in OSS; roles per project are Enterprise. 0017 puts SSO, SAML with enforcement and SCIM in Enterprise.
- 0024 keeps Clerk in the private Cloud control plane, for sign-up and billing, and has Cloud run the public image unchanged.
- ARCHITECTURE.md already names API keys per project, stored hashed.
- The console is served by web from the same origin (0011), so a cookie needs no cross-site setup.

## Options

1. **Built-in accounts.** Email and password, server-side sessions in Postgres behind an HttpOnly cookie, invitation links, organisation roles enforced on every route, and hashed project API keys for programmatic and MCP access. It works in the Compose stack with nothing else running. The cost: we own password storage, session expiry and the invite flow, and must test them.
2. **OIDC login only** (Google, GitHub, any provider). There are no passwords to store, but every install needs an identity provider before its first sign-in, which breaks one-command setup. It also blurs the line with SSO, which 0017 makes Enterprise.
3. **A hosted auth service** (Clerk, Auth0), the way the old Cloud did. It is fast to build, but it puts a third party in the sign-in path of every self-hosted install, against "nothing leaves your servers".
4. **Auth at a reverse proxy** (oauth2-proxy and forwarded identity headers). It needs no auth code in VoiceGateway, but it has no invites and no roles, so the done-when cannot be met. It also adds a component to the install.
5. **Better Auth** (floated earlier for the console sign-in). A TypeScript server library, so it adds a Node service next to the one Go binary (0009, 0013). Rejected for that.
6. **Keep the shared token.** Nothing to build, but there are no users and no roles. It is acceptable only as the interim it is.

## Decision

Option 1: built-in accounts with server-side sessions for people, and hashed project API keys for programs. SSO, SAML and SCIM stay Enterprise on top of the same session layer.

## Consequences

**How it works:**
- **Passwords** are hashed with argon2id (`golang.org/x/crypto/argon2`), never logged or returned. Sign-in failures are rate-limited per address and per account, counted in Postgres so several web replicas share one count, and answer the same message for an unknown email and a wrong password.
- **Sessions** use `alexedwards/scs` with its `pgxstore`, over the `pgxpool` web already holds. The store's `sessions` table comes from our own goose migration, not the library's snippet. A session is an opaque, random id in an HttpOnly, SameSite=Lax cookie, with idle and absolute expiry and renewal on sign-in. Signing out deletes the session server-side.
- **The cookie is `Secure` whenever the console is served over HTTPS.** That is the case when the configured public URL is `https`, or when a trusted proxy sends `X-Forwarded-Proto: https`. Browsers drop a `Secure` cookie set over plain HTTP on any host but `localhost`. So on plain HTTP away from localhost, the sign-in page says HTTPS is required instead of failing silently, and on `localhost` sign-in works over HTTP.
- **Cross-site requests:** writes are refused unless they come from the console's own origin, using `net/http`'s `CrossOriginProtection` (Go 1.25+). No CSRF tokens. In development, the Vite dev server proxies `/api` to web, so the console's writes come from the same origin there too.
- **First run:** `voicegateway user add --admin` creates the first admin, from the CLI as `project add` does today, so a fresh install has no open sign-up window. With no users, the console shows how to run it.
- **Invitations:** a single-use link with a random token, stored hashed, expiring in 7 days, scoped to one organisation and role. When SMTP is configured, web emails it; otherwise the admin copies it from Settings, so the Compose stack needs no mail server.
- **Roles:** Admin, Member and Viewer are checked by one gate on every route (0019). An unauthenticated request is 401, and a signed-in user outside the organisation, or without the role an action needs, is 403. Ids from another project stay `not_found`.
- **Project API keys:** for MCP, scripts and the SDK. A key has a visible prefix and a random secret, is stored as a SHA-256 hash, can be revoked, and is sent as `Authorization: Bearer`. A key is a long random secret checked on every request, so it takes a fast hash; argon2id is for passwords only. It replaces the interim `VOICEGATEWAY_API_TOKEN`, which is removed once keys land.
- **Cloud:** the control plane provisions an organisation and sends its first admin an invitation through the instance API. People sign in to the product with these same accounts; Clerk stays inside the control plane for the customer's billing account only.

**What a self-hosted install needs beyond `docker compose up`:**
- **TLS:** HTTPS in front of web for any use away from `localhost`, because of the cookie rule above. A reverse proxy or load balancer that sets `X-Forwarded-Proto` is enough.
- **The first admin:** one `docker compose exec web voicegateway user add --admin` (or its `make` target). The console's empty-users screen shows the exact command.
- **Mail:** optional. Without SMTP, invitations are copied by hand and an admin resets passwords.
- **Several web replicas:** nothing extra. Sessions and sign-in rate limits live in Postgres.

**Gets easier:**
- One install works end to end with no identity provider.
- The console and MCP share one access model.
- Cloud needs no fork.

**Gets harder:**
- We must maintain password hashing, sessions, invitations and rate limits.
- They need their own tests: a session cookie that cannot be read by script, a cross-origin write refused, an invitation used twice refused, and a Viewer refused a write.
- Cloud has two sign-ins. A customer signs up in Clerk for billing, then gets an invitation to set a product password. That is the cost of one image. The instance API that sends the invitation is in 0017's Enterprise row ("Organisation and instance management API"), so Cloud needs it in V1, though `ee/` is otherwise empty.

**Rules for CLAUDE.md, once accepted:**
- Never log a password, a session id, an invitation token or an API key secret.
- Every `/api/v1` route goes through the one access gate.
- Credentials are compared in constant time.

**Not decided here:**
- Password reset by email (it needs SMTP; without it, an admin resets a member's password).
- Two-factor sign-in.
- The exact session lifetimes.
- OAuth for remote MCP clients that expect it. A Bearer project key covers most clients.

Each of those is a spec decision, not a new record.
