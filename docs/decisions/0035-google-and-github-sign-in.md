# 0035: Google and GitHub sign-in, off unless configured

- Status: accepted
- Date: 2026-10-23

## Context

Decision 0027 chose built-in accounts (email, password, server-side sessions) and turned down OIDC-only login. With OIDC only, every install needs an identity provider before its first sign-in, which breaks one-command setup, and the feature blurs into SSO, which 0017 makes Enterprise. Decision 0028 then made a Cloud workspace an organisation on a shared instance, with people signing in through the product's own accounts.

On 2026-10-23 Mahimai asked for Google and GitHub sign-in on the console ("I would need both the google login and github login"). A Cloud customer pays, gets an invitation, and today must set a password before they see a call. Most of them already have a Google or GitHub account, and other developer tools in this space offer both.

0027's objections are to OIDC as the only way in. They do not apply when it is an optional second way in, on top of built-in accounts.

## Options

1. **Keep email and password only.** Nothing to build. Cloud customers keep a password step and one more password to manage.
2. **Google and GitHub as an optional second way in, in OSS.** Built-in accounts stay the base and work with nothing configured. Each provider turns on only when its client id and secret are set. A provider sign-in reaches an account only through a verified email that matches an existing account or a pending invitation. It never opens sign-up.
3. **The same, but Enterprise.** More to sell, but 0017 keeps Enterprise for what an organisation's security team needs (enforced SSO, SAML, SCIM). Signing yourself in with GitHub is not that, and Langfuse and PostHog give it away.
4. **Any OIDC provider, configured by issuer URL.** The most general, but that is SSO in all but name, which 0017 puts in Enterprise, and GitHub is OAuth 2.0, not OIDC, so it would still need its own path.

## Decision

Option 2. Google and GitHub sign-in are OSS and off by default. Each one is on only when its client id and secret are set. A provider sign-in never creates an account on its own: it signs in an existing account whose email matches the provider's verified email, or accepts a pending invitation for that email. Cloud turns both on.

## Consequences

- **Built-in accounts stay the base (0027 stands).** A fresh install with nothing set behaves exactly as today. Passwords, sessions, invitations and resets are unchanged, and every provider sign-in ends in the same scs session a password sign-in makes.
- **No open sign-up.** The first admin still comes from `voicegateway user add --admin` or the instance API (0028). On an install with users, a provider email that matches no account and no pending invitation is refused with the same answer as a wrong password, so the console does not reveal who has an account.
- **The verified email is the proof.** Google's ID token must carry `email_verified: true`. GitHub's email is the primary entry of `/user/emails` with `verified: true`. Anything else is refused. 0028's rule holds: an invitation reaches only the paid address, and a provider sign-in can claim it only with a verified copy of that same address.
- **Identities are stored by provider subject.** The first sign-in links the provider's stable user id to the account; later sign-ins use that id, not the email. Changing the email at the provider then neither locks the person out nor moves the link to someone else.
- **Libraries:** `golang.org/x/oauth2` for both providers, with PKCE, and `github.com/coreos/go-oidc/v3` to verify Google's ID token. No auth service, no proxy and no provider SDK.
- **Cloud needs two OAuth apps,** one in Google Cloud and one in GitHub, each with the callback under `https://cloud.voicegateway.dev`. Their secrets live in the host environment like the other secrets, never in a repository.
- **Enterprise stays Enterprise:** SAML, any-issuer OIDC, enforcing SSO (turning passwords off) and SCIM remain in 0017's Enterprise row.
- **Gets harder:** two more sign-in paths to keep tested, an `identities` table, and provider outages that a password does not have. A person who signed in only with a provider has no password until they set one through a reset.
- The build is phase 06h (`docs/specs/phase06h-social-sign-in.md`).
