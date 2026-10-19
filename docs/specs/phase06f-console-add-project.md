# Phase 06f: Add a project from the console

## Goal

An Admin of an organisation with no project adds a LiveKit project from the console: name, LiveKit URL, API key and API secret. A hosted admin (decision 0028: a workspace is an organisation on a shared instance) has no shell, so `voicegateway project add` cannot be their only way in.

## Context

- Decision 0016: a project is one LiveKit project. Decision 0019: Admin manages projects. Decision 0027: one gate on every `/api/v1` route, no secret in a log line. Decision 0028: one shared instance, a workspace is an organisation.
- `internal/app/projects.go` `AddProject`, which seals the secret under `VOICEGATEWAY_SECRET_KEY` through `pgstore.Store.CreateProject`.
- `dashboard/src/components/app-shell.tsx`: the "No project yet" empty state, which shows only the server command.

## In Scope

- **One rule for a new project**, `api.CheckProject`, used by `createProject` and `voicegateway project add`:
  - Every field is required. The name is trimmed, 1 to 64 characters, with no control characters and at least one letter or digit.
  - The id is the name's slug (`api.Slug`, moved from `internal/app`), cut to the 63 characters a project id may have. The command line's `--id` is checked against the table's id pattern.
  - The LiveKit URL parses with the scheme ws, wss, http or https, a host, and no user information, at most 2048 characters.
  - The API key is trimmed, at most 256 characters, with no spaces or control characters. It is scoped to the project; independent servers may use the same key string (decision 0032).
  - The secret is kept exactly as given, and only has to be more than blanks.
  - The command line names fields by its flags (`--livekit-url`, `LIVEKIT_API_SECRET`); the API by its JSON names.
- **`createProject`** `POST /api/v1/org/{orgId}/projects`, tagged Console writes, `x-access` Admin at organisation scope; a project key is 403.
  - Body `ProjectCreate` `{name, livekitUrl, livekitApiKey, livekitApiSecret}`, at most 4 KiB, unknown fields refused.
  - 201 with the contract's existing `WorkspaceProject` `{id, name}` and `Cache-Control: no-store`. Never the key or the secret.
  - 400 `invalid_request` for a bad body or field, with the rule's message. 409 `project_exists` for a taken id.
  - Before storage, a signed read checks endpoint connectivity. The supplied server can accept any token, so this is not proof of ownership. Tenant isolation comes from project-scoped ingestion URLs (0032). It is `ListRooms` under the room-list grant, signed with the given key and secret, through `scrape.DialThrough(scrape.NewWorkerClient(VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY))` (web's own copy of the flag), within 5 seconds. LiveKit refusing it is 422 `livekit_secret_invalid` ("the LiveKit API secret did not verify for this key"); any other failure, an address the guard refuses included, is 422 `livekit_unreachable` ("could not reach this LiveKit URL"), which never says which. The interface, `api.LiveKitVerifier`, lives in `internal/api`; `internal/app` wires `scrape.NewVerifier`, and without a verifier the route answers 403 as when it is off. `voicegateway project add` makes the same read, and `--skip-verify` (`make project-add SKIP_VERIFY=1`) leaves it out for a server not reachable yet.
  - The store interface, `api.ProjectCreator`, lives in `internal/api`; `store.NewProject` replaces `pgstore.NewProject` (kept as an alias). `internal/app` wires web's sealed store.
- **Console**:
  - When the organisation in the URL has no project, an Admin sees the Add project form, with the server command below it for a self-hosted install. Anyone else reads that an admin of the organisation must add one.
  - The secret is a password input and is cleared after every submit. On success the console opens the new project with a full page load, as "No such project" does, so the workspaces are read again. The API's error message shows under the form.
  - The same form replaces the disabled placeholder in the "Add a LiveKit project" dialog (Settings, Organisation, and the project switcher). Its unbacked "Observe live rooms" switch is removed.
  - The mock API serves `createProject`, keeping added projects in this browser so the page load finds them.

## Out of Scope

- An SSRF guard for the worker's LiveKit reads (see Open Questions).
- Editing, renaming or deleting a project, rotating its secret, or choosing an id other than the slug in the console.

## Constraints

- No new dependency: `net/url`, the existing `seal.Sealer` and store path, shadcn `Field`, `Input` and `Button`.
- Every new response validates against `docs/api/openapi.yaml` in the tests; `docs/api/readers.json` lists the new reader.

## Acceptance Checks

1. Given an Admin of organisation A, when they create a project, then the answer is 201 `{id, name}` with `Cache-Control: no-store` and neither the key nor the secret; the stored secret is not in the clear and opens to the original with the sealer; `getWorkspaces` lists the project (measured by `TestCreateProject`).
2. Given a name with spaces and capitals, then the id is its slug, stored under organisation A (measured by `TestCreateProject`).
3. Given a taken name, a Member, a Viewer, another organisation's Admin, a project key, no credential, missing fields, a blank secret, a URL with another scheme or no host, a name with no letter or digit, an unknown field, a body that is not JSON, one over 4 KiB, and a create from another site, then each answers 409 `project_exists`, 403, 403, 403, 403, 401, 400 (8 cases) and 403 respectively, and no row is stored (measured by `TestCreateProjectRefusals`).
4. Given every route, then each has its minimum role, matching the contract's `x-access` (measured by `TestEveryRouteHasAMinimumRole`, `TestProjectKeySecurityMatchesTheGate`).
5. Given checks 1 to 3, then every response validates against the contract (measured by the response validator in those tests).
6. Given the logs of a created project, a 409 and a failing store, then no line holds the LiveKit secret (measured by `TestNoSecretInLogs`, extended).
7. Given the console, then 4 Vitest cases pass: an Admin sees the form and a password secret field; a Member sees that an admin must add one, with no form; a submit sends the four fields, clears the secret and opens the new project; an error shows inline and the secret is cleared (measured by `add-project.test.tsx`).
8. Given `api.CheckProject` and `voicegateway project add`, then both refuse the same fields, each in its own names (measured by `TestCheckProject`, `TestAddProjectNamesWhatIsMissing`, `TestAddProjectChecksLikeTheAPI`).
9. Given livekit-server --dev, then a wrong secret and an unknown key are 422 `livekit_secret_invalid` with no row stored; the right secret is 201; another organisation claiming the stored key with a made-up secret is 422, not 409, and the same caller with the right secret can create its own project with 201; a URL with nothing listening is 422 `livekit_unreachable`; no log line holds a secret (measured by `TestCreateProjectChecksLiveKitConnectivity`).
10. Given `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true`, then a listening loopback server (by address, by name and as ws://), a closed loopback port, 10.0.0.7 and 169.254.169.254 each answer the same 422 `livekit_unreachable`, the listening server gets no request and no row is stored (measured by `TestCreateProjectGuardedReadRevealsNoAddress`).
11. Given a verifier's outcome, then createProject answers 201, 422 `livekit_secret_invalid` or 422 `livekit_unreachable` with a fixed message and stores only when verified; without a verifier it is 403; `voicegateway project add` refuses an unreachable URL naming `--skip-verify`, and skips the read with it (measured by `TestCreateProjectAnswersTheVerifier`, `TestCreateProjectDisabled`, `TestAddProjectVerifiesUnlessSkipped`; the console shows both 422 messages inline in `add-project.test.tsx`).

## Manual Check

On the Compose stack, sign in as an admin of an organisation with no project, add a project from the empty state, and land on its overview; then try the same name again and read the 409 under the form.

## Cloud rollout

Console project creation is enabled in both editions. Cloud sets `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true` so every worker LiveKit read uses the dial-time address guard.

## Open Questions

- Resolved in the follow-up (progress.md, 2026-10-19: worker LiveKit dial guard): with `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true` the worker's LiveKit reads dial through `code.dny.dev/ssrf`, over livekit/protocol's Twirp clients with our guarded `http.Client` (server-sdk-go v2.19.0 has no option to pass one, except for `AgentClient`), and a refused address is stored as `unreachable`, the same class as a closed port, so the layer's error class no longer reveals reachability. The Cloud owner accepted the loss of SDK region failover and outbound proxy support for the initial Lightsail deployment. Failed reads retain unknown/error status and retry on the next poll; they must never report a successful zero.
