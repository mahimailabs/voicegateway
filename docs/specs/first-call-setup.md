# First-call setup: example agent and connection check

## Goal

A developer with a LiveKit agent copies two URLs from the console, runs the documented example (or sets one variable in their own agent), and watches the console move from "waiting" to "first call stored", with a plain fix shown for every way the connection can fail.

Plan board tasks: dev-sdk ("The documented example sends a real call to both a self-hosted endpoint and a hosted project.") and dev-first-call-verification ("An unfamiliar user follows the Cloud setup without assistance, sees actionable connection errors and confirms their own first stored call. Credentials respect project scope and revocation; a sample trace is never counted as activation.").

## Context

- PRODUCT.md: first call and onboarding.
- ARCHITECTURE.md: web owns request state in Postgres; the worker alone writes the call index.
- Decisions 0014 (observability endpoints), 0027 (sign-in and access), 0032 (project-scoped ingestion), 0033 (LiveKit's uploader is the agent connection, amends 0002).

## In Scope

A. Documented example (dev-sdk)

1. `examples/livekit-agent/`: the smallest Python LiveKit Agents voice agent on LiveKit Inference (needs only the LiveKit URL, key and secret). `pyproject.toml` with exact pins, `uv.lock`, `.env.example` naming `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_OBSERVABILITY_URL`. ruff, ruff format and mypy in pre-commit, scoped to that folder. No VoiceGateway import.
2. A scripted caller so no human is needed, through LiveKit's own tooling (the `lk` CLI) into a room the agent is dispatched to. Hand-written only if no `lk` command fits; the PR says why.
3. `make example-call` runs the agent and the caller for one call against whatever `.env` points at.
4. site/docs "Connect your agent": the example, the one variable for an existing Python or Node agent, the webhook URL. Same text for self-hosted and Cloud; only the host differs.

B. Connection check (dev-first-call-verification)

5. Ingest records each project's connection outcome per source (`webhook`, `agent`): last accepted at, last rejected at and the reason code. Postgres table `project_connections` (00019), written by web as request state. Throttled in process: per project and source, a write happens only when the outcome differs from the last one written or 30 s have passed since it, so a busy agent adds no per-upload database round trip. The throttle is a bounded LRU (hashicorp/golang-lru) holding each outcome's write time; `expirable` was not used because its cleanup goroutine never exits, which `testing/synctest` cannot run. A project that does not exist is remembered for 30 s in its own small LRU, so made-up URLs cost one round trip per window and never evict a real project. Each write is bounded to 2 s.
6. Reason codes, each with a fix shown in the console:
   - `token_missing`: no `Authorization` header or bearer token.
   - `key_mismatch`: the token's LiveKit API key is not this project's. The console shows the key used and the key expected; API keys are not secret, secrets are never shown. The sender chooses the key, so it is kept only in a LiveKit key's shape (`^[A-Za-z0-9_-]{1,64}$`) and is otherwise unknown ("an unreadable key").
   - `signature_invalid`: the right key with the wrong secret, an expired token, or a token that does not parse.
   - `grant_missing`: an upload token without the observability write grant (LiveKit Agents too old).
   - `body_changed`: a webhook whose body no longer matches the signed hash.
   - `plan_inactive` / `plan_cap`: 402 on Cloud; inactive or expired plan, or minutes used up. Billing link.
   - `too_large`: 413.
   An unknown organisation or project is refused as today and records nothing: the write selects the project row, so it inserts nothing for a project that does not exist.
7. `GET /api/v1/org/{orgId}/project/{projectId}/connection` (Viewer, project scope, through `handle` and roles.go): `{ urls: { observability, webhook }, webhook: Source, agent: Source, firstCall: { id, startedAt } | null }`. `Source` is `{ lastAcceptedAt, lastRejectedAt, reason, usedKey, expectedKey }`, every field nullable. URLs come from `VOICEGATEWAY_PUBLIC_URL`, null when unset; the console then says to set it. Contract in openapi.yaml with x-source and x-access, then `make api-types`.
8. `firstCall` is the earliest row in the calls index, which only the worker writes from verified ingest. Fixtures, MSW mocks and demo data never touch it, so a sample trace never counts.
9. Console: an empty project opens a "Connect your agent" panel on the project page: copy buttons for both URLs; three steps (Webhook, Agent upload, First call stored) each Waiting, Connected or Error; the error's fix text; a link to the first call. Polls every 5 s until `firstCall` exists and no source is in Error (TanStack Query `refetchInterval`). An unauthenticated request must not turn a working source red: a source whose last accept is newer than its last refusal is Connected. After a source has had any accept, a later refusal that anyone can send (`token_missing`, `key_mismatch`, `signature_invalid`, `too_large`, or no reason) keeps it Connected with the refusal and its fix as a warning, however old the accept; a signature-verified refusal (`grant_missing`, `body_changed`, `plan_inactive`, `plan_cap`) is that warning while the accept is at most 10 minutes old, and Error after that. A source with no accept is in Error on any refusal. Overview shows the panel while the project has no call or a source is in Error; Settings > Connection always has it. Pre-built components only.

## Out of Scope

- The optional `attach()` SDK (0033; its own spec), frameworks other than LiveKit Agents, changing call joining.

## Constraints

- livekit/protocol `auth` and `webhook` verification as ingest already uses; their error values pick the reason code.
- Never log or store raw bodies, secrets or tokens. The key a refused token named is stored, truncated to 64 bytes.
- Unknown stays null, never zero.

## Acceptance Checks

1. The example passes ruff, mypy and its lock; `make example-call` against a self-hosted stack stores one call whose turns open in the console after reload (call id in docs/progress.md).
2. The same against a Cloud project stores one call there (call id recorded).
3. TestConnectionReasons: each failure in item 6 answers its current status code, appends nothing, and the endpoint returns that reason; a valid request then shows accepted.
4. TestConnectionOtherTenant: a request signed for project A sent to B's URL is recorded only on B (`key_mismatch`, or `signature_invalid` if the keys collide); A is unchanged.
5. TestConnectionRevoked: after the project's LiveKit secret is replaced (or the entitlement goes inactive) the old credentials are refused and the reason shown; nothing is stored.
6. TestConnectionThrottle (testing/synctest): 100 accepted uploads in 30 s produce one write.
7. TestFirstCallIgnoresFixtures.
8. Console Vitest per step state; Playwright and axe on the panel at phone and desktop widths.
9. A fresh reader follows the Cloud docs unaided (done later by the Pair thread).
10. roles.go, the router and the contract's x-access agree (existing test).
