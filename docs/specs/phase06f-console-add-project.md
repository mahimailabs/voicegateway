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
  - The API key is trimmed, at most 256 characters, with no spaces or control characters. It is unique on the server, as before.
  - The secret is kept exactly as given, and only has to be more than blanks.
  - The command line names fields by its flags (`--livekit-url`, `LIVEKIT_API_SECRET`); the API by its JSON names.
- **`createProject`** `POST /api/v1/org/{orgId}/projects`, tagged Console writes, `x-access` Admin at organisation scope; a project key is 403.
  - Body `ProjectCreate` `{name, livekitUrl, livekitApiKey, livekitApiSecret}`, at most 4 KiB, unknown fields refused.
  - 201 with the contract's existing `WorkspaceProject` `{id, name}` and `Cache-Control: no-store`. Never the key or the secret.
  - 400 `invalid_request` for a bad body or field, with the rule's message. 409 `project_exists` for a taken id. 409 `livekit_key_in_use` for a key another project on the server uses, without naming that project, which may be another organisation's.
  - The store interface, `api.ProjectCreator`, lives in `internal/api`; `store.NewProject` replaces `pgstore.NewProject` (kept as an alias). `internal/app` wires web's sealed store.
- **Console**:
  - When the organisation in the URL has no project, an Admin sees the Add project form, with the server command below it for a self-hosted install. Anyone else reads that an admin of the organisation must add one.
  - The secret is a password input and is cleared after every submit. On success the console opens the new project with a full page load, as "No such project" does, so the workspaces are read again. The API's error message shows under the form.
  - The same form replaces the disabled placeholder in the "Add a LiveKit project" dialog (Settings, Organisation, and the project switcher). Its unbacked "Observe live rooms" switch is removed.
  - The mock API serves `createProject`, keeping added projects in this browser so the page load finds them.

## Out of Scope

- An SSRF guard for the worker's LiveKit reads (see Open Questions).
- Editing, renaming or deleting a project, rotating its secret, or choosing an id other than the slug in the console.
- Checking the credentials against LiveKit before storing them.

## Constraints

- No new dependency: `net/url`, the existing `seal.Sealer` and store path, shadcn `Field`, `Input` and `Button`.
- Every new response validates against `docs/api/openapi.yaml` in the tests; `docs/api/readers.json` lists the new reader.

## Acceptance Checks

1. Given an Admin of organisation A, when they create a project, then the answer is 201 `{id, name}` with `Cache-Control: no-store` and neither the key nor the secret; the stored secret is not in the clear and opens to the original with the sealer; `getWorkspaces` lists the project (measured by `TestCreateProject`).
2. Given a name with spaces and capitals, then the id is its slug, stored under organisation A (measured by `TestCreateProject`).
3. Given a taken name, a taken LiveKit key, a Member, a Viewer, another organisation's Admin, a project key, no credential, missing fields, a blank secret, a URL with another scheme or no host, a name with no letter or digit, an unknown field, a body that is not JSON, one over 4 KiB, and a create from another site, then each answers 409 `project_exists`, 409 `livekit_key_in_use`, 403, 403, 403, 403, 401, 400 (8 cases) and 403 respectively, the `livekit_key_in_use` message names no project, and no row is stored (measured by `TestCreateProjectRefusals`).
4. Given every route, then each has its minimum role, matching the contract's `x-access` (measured by `TestEveryRouteHasAMinimumRole`, `TestProjectKeySecurityMatchesTheGate`).
5. Given checks 1 to 3, then every response validates against the contract (measured by the response validator in those tests).
6. Given the logs of a created project, a 409 and a failing store, then no line holds the LiveKit secret (measured by `TestNoSecretInLogs`, extended).
7. Given the console, then 4 Vitest cases pass: an Admin sees the form and a password secret field; a Member sees that an admin must add one, with no form; a submit sends the four fields, clears the secret and opens the new project; an error shows inline and the secret is cleared (measured by `add-project.test.tsx`).
8. Given `api.CheckProject` and `voicegateway project add`, then both refuse the same fields, each in its own names (measured by `TestCheckProject`, `TestAddProjectNamesWhatIsMissing`, `TestAddProjectChecksLikeTheAPI`).

## Manual Check

On the Compose stack, sign in as an admin of an organisation with no project, add a project from the empty state, and land on its overview; then try the same name again and read the 409 under the form.

## Cloud rollout

With `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true`, console project creation returns 403 until the worker LiveKit dial guard lands. Self-hosted project creation remains enabled. The follow-up removes this temporary gate only with guarded reads.

## Open Questions

- Resolved in the follow-up (progress.md, 2026-10-19: worker LiveKit dial guard): with `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true` the worker's LiveKit reads dial through `code.dny.dev/ssrf`, over livekit/protocol's Twirp clients with our guarded `http.Client` (server-sdk-go v2.19.0 has no option to pass one, except for `AgentClient`), and a refused address is stored as `unreachable`, the same class as a closed port, so the layer's error class no longer reveals reachability. Still open: that mode drops the SDK's LiveKit Cloud region failover and ignores proxy settings; Cloud must confirm it needs neither before it opens this form.
