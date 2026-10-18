# V1 Connect: implementation order and repository cleanup

Status: proposed execution plan, 15 October 2026. The eight board items are outcomes, not eight equally sized PRs. Dates remain targets. Phase 1 and Cloud are not complete.

## Current evidence

The console renders captured fixtures. PR #329 restores the missing tracked helpers, supplies root Makefile commands, validates saved thresholds, adds five helper regressions and verifies a clean-checkout build in CI. Calls-list and call-detail UI tests remain #324. OpenAPI, generated types and contract-backed mocks remain #321, #322 and #323. No new Go core exists yet. Existing Cloud identity, provisioning and Stripe code is scaffolding to integrate, not proof of a new hosted call.

The first product milestone is a real LiveKit call crossing the authenticated ingest boundary, surviving storage and reload, and opening in the current console with source-attributed turns and unknown values preserved.

## Repository audit and cleanup boundaries

Note: Phase 0 (2026-10-18) retired the old Python release. The lines below marked superseded describe the state before it; docs/contributing/ and the legacy files they name are gone.

- Keep root entry points and discoverable project documents: README, LICENSE, SECURITY, CODE_OF_CONDUCT, CONTRIBUTING, AGENTS, CLAUDE, PRODUCT, ARCHITECTURE and DESIGN. Keep the Makefile and tool-recognised dotfiles.
- Superseded by Phase 0 (2026-10-18): Consolidate the detailed contributor guides in `docs/contributing/`. Preserve a short root CONTRIBUTING entry point. Put the optional legacy MCP example beside the legacy guides; retain the active tracked V1 `.mcp.json` tooling configuration.
- Keep `dashboard/` as the V1 console. Superseded by Phase 0 (2026-10-18): `src/dashboard/frontend/` still belongs to the released Python product; it is not a second V1 console to maintain indefinitely.
- Keep `site/landing/` as the Pages frontend, `site/docs/` as the documentation app and `site/web/` as the existing D1/API Worker. Landing builds currently reuse that Worker's captured demo assets. Do not delete the Worker or its database when simplifying marketing files.
- Superseded by Phase 0 (2026-10-18): Keep `pyproject.toml`, `uv.lock`, `alembic.ini`, `alembic/`, the root Compose files, `railway.json`, legacy Dockerfile and publish workflows until the released Python runtime is deliberately retired. Packaging force-includes Alembic; collector CI consumes the root Compose files; Railway references the Python Dockerfile. These files are actively referenced, not deletion candidates today.
- Do not move `install.sh` or `collector.sh`: public download paths and site builds consume them. Change implementation behind those paths only with install proof.
- Superseded by Phase 0 (2026-10-18): Phase 0 retirement needs a replacement install, migration/rollback policy and release decision. Remove obsolete runtime, deployment configuration, old frontend and their CI together in that PR; do not leave broken references or move hundreds of dead files into an archive directory. Git tags preserve source history.

The sanitation PR changes documentation layout and stale guidance only. It does not create empty Go packages, delete active runtime code, change migrations, or rename public installer URLs.

## Intended V1 ownership

Create these paths only as working code arrives:

| Path | Responsibility |
| --- | --- |
| `cmd/voicegateway/` | Thin web, worker, migrate and preflight entry points |
| `internal/` | Flat Go packages named for their work; app owns wiring |
| `internal/event`, `internal/stream`, `internal/store` | The limited shared contracts already selected by architecture |
| `dashboard/` | Console, generated API types, query/client layer and UI tests |
| `sdk/python/` | Agent-side SDK; never a second backend |
| `deploy/` | V1 Compose, image and operational configuration |
| `docs/api/` | OpenAPI and source attribution |
| `docs/decisions`, `docs/specs`, `docs/contributing` | Why, acceptance criteria and contributor guidance |
| `site/` | Landing, documentation and existing waitlist API |
| `tools/` | Small build, generation and verification scripts |

The Cloud control plane stays in its existing repository. Integrate its identity/payment/provisioning boundaries with the Go product API. Do not duplicate the product API or assume a Cloud rewrite is necessary.

## Eight Connect outcomes

| Board outcome | Reviewable PR slices | Dependencies and completion proof |
| --- | --- | --- |
| Lock v1 contracts | #321: call/project/event schemas and source attribution; #322: generated TypeScript; #323: contract-backed MSW fixtures. Define call identity, time units, nullability, auth/error envelopes and paging. | Review with ingest, console and Cloud owners. Schema lint and source checks pass, generation is reproducible, mocks conform, and no parallel hand-written API types remain. |
| Capture the first real call | Authenticated ingest and verified LiveKit webhook/upload decoding; Redis consumer, call joining, durable writes and acknowledgement after insert. | Depends on contracts, a runnable stack and minimal project/key storage. Exercise one real call, duplicate delivery, reused rooms and restart; reported turn values survive reload without fabricated zeros or silent loss. |
| Connect the console | Calls/list/detail read API; console queries and loading/empty/error states. Preserve URL state and permissions. | Depends on read contracts and stored call evidence. A fresh call appears and opens after reload with fixture substitution disabled. #324 UI tests and #325 shell/accessibility checks supply proof. |
| Wire teams and projects | Project records, encrypted LiveKit secrets and hashed project keys first; login, org membership, invitations and role-enforced APIs next; console switching integration. | Minimal project scoping precedes ingest. Full membership can proceed alongside call ingestion once contracts are fixed. Cross-project and unauthorised-role access fails at the server boundary. |
| Automate Cloud workspace creation | Existing identity-to-workspace provisioning integrated with Go; verified Stripe subscription/entitlement and first-call journey. | Depends on project isolation, an operational API and the chosen Cloud charging unit. Idempotent retries do not duplicate workspaces; signup/payment/activation need no founder approval. Reuse current Cloud code where it fits. |
| Connect LiveKit layers | Separate adapter PRs for media server, SIP, egress/ingress and agent-worker metrics; connect each to shared read contracts. | Depends on project connections and source schemas. Each supported layer exposes real state, unavailable telemetry stays unknown, and provider/API failures have visible states. Avoid one giant all-pages PR. |
| Define alert behaviour | One contract/spec PR for the existing seven checks: thresholds, grouping, deduplication, recovery, delivery retries and history. | Can proceed alongside contract work. Captured triggering/recovery examples are agreed by owners. This week-1 definition does not claim that week-2 notification delivery is implemented. |
| Make preview installs repeatable | Go service/config/preflight and a pinned Compose stack; migrations, image build and fresh-environment first-call smoke test. | Foundation begins before live ingestion. Dashboard CI alone is only a sub-milestone. A teammate installs the actual preview from tracked source and CI exercises the supported stack and migration path. |

## Execution order

1. Merge and record the console-baseline repair, then the documentation/layout cleanup. Neither completes the eight broad items.
2. Freeze #321, then land #322 and #323. Implement #324 and #325 against the agreed console behaviour. Review unresolved product choices explicitly rather than hard-coding assumptions.
3. Build the runnable Go/storage foundation and minimal project/key boundary. Agree alert behaviour in parallel with this work.
4. Complete one vertical slice: real call -> authenticated ingest -> Redis -> durable write -> read API -> console. Keep incomplete features honestly unavailable.
5. With that slice passing, split ownership across full teams/access, LiveKit adapters and Cloud integration. Freeze shared contracts before parallel changes and resolve dependencies through small PRs.
6. Close board outcomes only when their own acceptance checks pass. Continue week-2 actions and week-3 reliability, isolation, payment and release proof; the first working call alone is not release readiness.

## Using goal mode

Use a bounded goal for one outcome with explicit scope and proof; a goal can produce more than one PR. Do not treat one board checkbox as a mandatory single PR. A goal ends when its observable result is achieved, not when a token budget is exhausted.

Every implementation goal should state: base branch, owned files, dependencies, acceptance checks, required validation, explicit exclusions and stopping conditions. Branches use `feat/` or `fix/`; commits use the maintainer's identity. Worktrees isolate concurrent changes. No goal may silently change the public API, access policy, billing unit, storage ownership or supported release behaviour.

Prefer framework/library facilities and existing code. Keep constructor injection at wiring, interfaces with their consumers, SDK capture fail-open, web/worker ownership intact, unknown values explicit, and schemas/types generated from their source. Add tests for meaningful behaviour and failures, not tests that repeat implementation details. Do not add generic managers, factories, service wrappers or empty package scaffolding for possible future features.

Recommended first implementation goal after cleanup: finish the source-attributed dashboard API contract (#321), with lint/source checks and agreement on call identity and project scoping. It unlocks the generated types, mock API and independent backend/console work.
