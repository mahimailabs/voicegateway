# 0036: Auth0 sign-in, an instance-wide option beside Google and GitHub

- Status: accepted
- Date: 2026-10-23
- Amends: 0035

## Context

Decision 0035 added Google and GitHub sign-in, each talking to its provider directly. It turned down "any OIDC provider, configured by issuer URL", because that is SSO in all but name, and 0017 puts SSO in Enterprise.

On 2026-10-23 Mahimai chose Auth0 for Cloud sign-in, since VoiceGateway can get startup credits there. Auth0 sits between the console and the providers: its hosted login page offers the connections the tenant has on (Google, GitHub, others), and it answers one OpenID Connect ID token whichever one the person used.

## Options

1. **Direct Google and GitHub only (0035 as it stands).** Free, already built. Cloud does not use Auth0.
2. **Auth0 as a third provider, configured by its tenant domain.** The operator of an instance runs one Auth0 tenant for everyone on it. VoiceGateway talks to Auth0 only, through the same OpenID Connect path as Google.
3. **Any OIDC issuer.** Covers Auth0 too, but it is 0035's option 4, which stays Enterprise.

## Decision

Option 2. An Auth0 application is OSS and off by default, like Google and GitHub, and turns on when `VOICEGATEWAY_AUTH0_DOMAIN`, `VOICEGATEWAY_AUTH0_CLIENT_ID` and `VOICEGATEWAY_AUTH0_CLIENT_SECRET` are all set. Every 0035 rule holds for it: no open sign-up, a verified email is required, and the identity is linked by its subject. Cloud uses Auth0. The direct Google and GitHub providers stay, for self-hosters who want no third party.

## Consequences

- **It is not Enterprise SSO.** One tenant serves the whole instance, chosen by its operator. An organisation cannot bring its own identity provider, enforce one, turn passwords off, or provision people through SCIM; all of that stays in 0017's Enterprise row. VoiceGateway takes a bare Auth0 domain, not any issuer URL, so this does not become option 3.
- **The proof is Auth0's ID token,** verified with `github.com/coreos/go-oidc/v3` against the tenant's issuer (`https://<domain>/`, with the slash) and keys, with the client id as audience, plus the nonce and PKCE. `email_verified` must be true. Auth0 copies it from the connection, so a connection that does not verify email is refused.
- **Identities are stored as `auth0`, by Auth0's user id** (`google-oauth2|…`, `github|…`). One person signing in with Google directly and with Google through Auth0 has two links to the same account, each made through a verified email.
- **Endpoints are fixed from the domain** (`/authorize`, `/oauth/token`, `/.well-known/jwks.json`), so web makes no call to Auth0 at start, the same as Google.
- **Cloud needs an Auth0 tenant** with a Regular Web Application whose Allowed Callback URL is `https://cloud.voicegateway.dev/auth/oauth/auth0/callback`. Its Google and GitHub connections need VoiceGateway's own OAuth apps, because Auth0's development keys are for testing only. Those apps call back to `https://<tenant>/login/callback`.
- **Gets harder:** a third-party service between Cloud and its sign-in, a per-user bill once the credits end, and one more provider to keep tested.
- The build is part of phase 06h (`docs/specs/phase06h-social-sign-in.md`).
