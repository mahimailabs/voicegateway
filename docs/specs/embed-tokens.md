# Embed tokens and compact widgets

## Goal

A platform built on LiveKit shows this project's widgets inside its own product: its backend asks VoiceGateway for a short-lived token, and its page frames a widget with it. Plan task dev-embeds: an approved origin renders the intended project while expired tokens, disallowed origins and other projects are rejected.

## Context

PRODUCT.md capability 9, decisions 0017, 0023, 0027 and 0034, docs/api/openapi.yaml (`createEmbedToken`, `updateAllowedOrigins`, `x-embed-access`).

## In Scope

- Allowed origins per project (Admin), stored in Postgres (00020), read in getProjectSettings.
- `createEmbedToken` for a project key or a member; tokens checked by the gate on every request.
- `frame-ancestors` and `Referrer-Policy` on console pages; an embed token reads its widgets' routes only.
- Compact widgets at `/org/<org>/project/<project>/embed/<widget>?token=&size=&theme=`: answer-time (small, medium), calls (small, medium), issues (small, medium, large), recent-calls (medium, large), time-per-turn (medium, large), and call (the one-call view a recent call opens).
- Settings, Embeds: widget, size and theme picker with a live preview, the token request and iframe snippets, allowed sites, and a test token.

## Out of Scope

- Tokens scoped to one end customer and widgets without the VoiceGateway mark (Enterprise, Cloud).
- Revoking one token on its own.

## Constraints

- golang-jwt v5 (already in the module graph through livekit/protocol), crypto/hkdf, golang.org/x/net/idna for origins.
- Pre-built console components only: shadcn Card, Item, Chart, Tremor CategoryBar, Gauge UI.

## Acceptance Checks

1. An approved origin renders the intended project: a token for https://app.example.com gets `frame-ancestors https://app.example.com` on its widget page and reads that project's calls (TestEmbedTokensFrameOneProjectForOneOrigin).
2. Expired tokens are rejected: 401 on the API and `frame-ancestors 'self'` on the page.
3. Disallowed origins are rejected: minting for an origin not allowed is 400 origin_not_allowed, and removing an origin stops live tokens.
4. Other projects are rejected: a token is 403 on another project's routes and `'self'` on its widget pages, and another project's key cannot mint here.
5. A token reads only its widgets' routes, never settings, keys, members, workspaces or a new token.
6. Revoking the minting key, or removing the minting member, stops the token.
7. In Chromium, a page on an allowed origin shows the widget and a page on another origin is blocked by the browser.
