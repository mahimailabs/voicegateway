# 0034: Embed tokens signed by VoiceGateway

- Status: accepted
- Date: 2026-10-21

## Context

Decision 0023 put embeddable widgets in the open source core, behind a short-lived read-only token and an origin allow-list, and left the token's shape to the server work. A widget frame polls the API every 30 to 60 seconds, so the check runs often; a platform's backend mints a token on each page load, so minting runs often too. The token must name one project, the widgets it opens and the site that may frame them, and it must stop working when the key that minted it is revoked or the site is removed, not only when it expires.

The maintainer also asked (2026-10-21) for the widgets to be compact, like iPhone widgets, instead of the full-page views 0023 started with.

## Options

1. An opaque random token stored hashed in Postgres, like a project key: easy to revoke one token, but a write per page load and a table that grows with every visitor.
2. A token the platform signs itself with a shared secret (Metabase-style): no call to VoiceGateway, but VoiceGateway stores only the SHA-256 of a key's secret, so it cannot verify such a signature without storing a second secret.
3. A JWT VoiceGateway signs on request (HS256, key derived from VOICEGATEWAY_SECRET_KEY with HKDF), stored nowhere, and checked against Postgres on every request: still minting key or member, origin still allowed.

## Decision

Option 3. `POST .../embed-tokens` (a project key or a member, Viewer and up) returns `vge_` followed by a JWT from golang-jwt v5, the library LiveKit's own access tokens use. Its claims name the organisation, project, widgets, one origin, the minter (`key:<key_id>` or `user:<id>`) and an expiry of 1 minute to 1 day (1 hour by default). The gate accepts HS256 only, with issuer, audience and expiry required, then one indexed query checks that the minter is still active and the origin still in the project's allowed origins, which an Admin sets (`PUT .../settings/allowed-origins`, PG 00021). Missing configuration allows no origin.

The widget page sends `Content-Security-Policy: frame-ancestors <origin>` only for a valid token naming that project and widget, and `frame-ancestors 'self'` everywhere else in the console, plus `Referrer-Policy: no-referrer` on widget pages. An embed token reads only the routes its widgets need (`embedAccess` in internal/api/roles.go, compared with the contract's `x-embed-access`) and cannot mint another token.

What was said in a call stays inside VoiceGateway unless the minter asks otherwise. A recent-calls frame opens the one-call view, and that view could show the call's transcript, span attributes (prompts, tool arguments, transcripts) and recording audio to anyone who can see the framing site. So a token carries a separate opt-in, `transcriptsAndAudio`, off by default. Without it, getCall answers every turn's text and every span's attributes as null, returns no SDK call fields (the agent's own data, such as customer ids) and leaves out the recording, and the recording route and listCalls' field filter refuse the token; the view shows timings, models and issues only and says so. Only an Admin or a project key may mint a token with the opt-in, it needs the call widget, and Settings, Embeds explains what turning it on shares.

The widgets become five compact tiles in small, medium and large sizes (answer time, calls, issues, recent calls, time per turn) plus the one-call view a recent call opens. This replaces 0023's widget list; the rest of 0023 stands.

## Consequences

- Revoking a key, removing a member or removing a site stops their tokens at the next request; a token cannot be revoked on its own, which a short lifetime covers.
- Changing VOICEGATEWAY_SECRET_KEY ends every embed token.
- The check costs one Postgres round trip per widget request, the same as a project key.
- A token is a bearer credential: whoever holds it reads its widgets' data until it expires. The origin check protects framing in a browser, not a copied token.
- With `transcriptsAndAudio` on, the framing site and everyone who uses it read what callers and the agent said, the prompts and tool arguments, and the recording, for every call in the project, until the token expires. That is the platform's choice to make for its own users; VoiceGateway does not filter it further.
- Per-customer tokens and removing the branding stay Enterprise and Cloud (0023).
