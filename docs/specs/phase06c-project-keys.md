# Phase 06c: Project API keys

## Goal

An admin creates an API key for one project in the console or from the command line, sees its secret once, and can list and revoke it. A script or coding agent sends the key as `Authorization: Bearer` and reads that project's `/api/v1` data, and nothing else. The interim `VOICEGATEWAY_API_TOKEN` is retired. This is the third phase for decision 0027, after 06a (sign-in) and 06b (members and roles), with 06d (invitation links) to follow.

## Context

- Decision 0027, "Project API keys":
  - A key has a visible prefix and a random secret.
  - It is stored as a SHA-256 hash, can be revoked, and is sent as `Authorization: Bearer`.
  - It is a fast hash, not argon2id, because a key is a long random secret checked on every request.
  - Keys replace `VOICEGATEWAY_API_TOKEN`.
  - The CLAUDE.md rules apply: never log a key secret, one gate on every route, and constant-time comparison.
- Decision 0019: Admin manages "settings, projects, keys and members".
- ARCHITECTURE.md: API keys belong to a project and are stored hashed in Postgres.
- `docs/specs/phase06b-members-and-roles.md`: the role table in `internal/api/roles.go`, the `allow` middleware, and how the interim token is gated today (project reads only, 403 on organisation operations, 401 on `getWorkspaces`).
- `internal/auth/auth.go`: `Gate`, which identifies a session or the interim token and puts a `store.Principal` on the request.
- `dashboard/src/features/settings/settings-dialog.tsx`: the API keys tab under Access, with a disabled "Create key" button per project.

## In Scope

- **Postgres** (one goose migration, sqlc): `project_keys` with these columns:
  - id;
  - organisation and project (foreign key to the project);
  - name;
  - `key_id`, the public part, unique;
  - SHA-256 of the secret;
  - created by (the user, null when made from the CLI);
  - created at;
  - last used at;
  - revoked at.
- **The key format:** `vgk_<key_id>_<secret>`.
  - `key_id` is 12 base32 characters from `crypto/rand`, used to find the row and shown in lists.
  - The secret is 32 bytes from `crypto/rand`, base64url without padding.
  - Only the SHA-256 of the secret is stored. The full key appears exactly once, in the create answer.
- **The gate** (`internal/auth`): a Bearer value starting `vgk_` is a project key.
  - The gate looks the key up by `key_id` and compares SHA-256 hashes with `subtle.ConstantTimeCompare`.
  - A malformed, unknown or revoked key is 401 `unauthenticated`, and all three look the same. An unknown `key_id` is compared against a dummy hash, so the timing is the same too.
  - A valid key puts a principal on the request that names its organisation, its project and the key, and no user.
  - `last_used_at` is updated at most once a minute per key, so a busy script does not write on every request.
- **What a key may do** (the `allow` middleware in `internal/api/roles.go`):
  - Every project-scoped read of its own project, as a Viewer.
  - Any other project's path is 403 `forbidden`, the same answer as a non-member gets.
  - Organisation-scoped operations are 403 (members, invitations, keys).
  - `getWorkspaces` answers with the key's organisation and its one project, so a client can find its scope.
  - A key never reaches a write.
- **New operations** in `docs/api/openapi.yaml`, tagged Console writes or Console reads, each with `x-access` `minimumRole: Admin`, `scope: project`:
  - `listProjectKeys` `GET /api/v1/org/{orgId}/project/{projectId}/keys`:
    - `{items: [{id, name, prefix, createdAt, createdBy, lastUsedAt, revokedAt}]}`, newest first, revoked keys included.
    - `prefix` is `vgk_<key_id>`. `createdBy` is the creator's email, or null for the CLI.
    - `lastUsedAt` and `revokedAt` are null when unknown or not revoked.
  - `createProjectKey` `POST` on the same path with `{name}` (1 to 64 characters): 201 with the key's list fields plus `secret`, the full key, returned only here.
  - `revokeProjectKey` `DELETE .../keys/{keyId}`: 204. It is idempotent, so revoking twice is 204. A key id from another project is 404.
  - A `ProjectKey` schema and a `CreatedProjectKey` schema, each with `x-source`.
  - The operation-tags rule from 06b already admits writes.
- **`voicegateway key create --org <o> --project <p> --name <n>`**: prints the full key once, for installs without a console session, such as CI. Also `voicegateway key revoke --org <o> --project <p> --id <prefix>`.
- **Retiring `VOICEGATEWAY_API_TOKEN`:**
  - The gate stops accepting it.
  - `config` no longer reads it, and `deploy/compose.yaml` and `tools/scripts/dev-env.sh` drop it.
  - When the variable is still set, web logs one warning at start, naming the variable and `voicegateway key create`, and starts. A left-over `.env` does not stop an upgrade.
  - The migration note goes in `docs/progress.md` and the README's upgrade section: create a key per project, replace the token in scripts, delete the variable.
- **Console** (Settings, Access, API keys), with `VITE_MOCK_API=off`:
  - Per project, the list from `listProjectKeys`.
  - "Create key" asks for a name, then shows the full key once with the existing `CopyCommand` and a line saying it will not be shown again.
  - Revoke asks first, as removing a member does.
  - A Member or Viewer sees the tab say keys are managed by admins, with no list.
  - The mock serves the three operations in memory.

## Out of Scope

- Using project keys for ingest from the SDK or OTLP. Ingest keeps its own checks: LiveKit's observability token for agent uploads, webhook signatures for LiveKit.
- The MCP server, which will read with these keys.
- Key expiry, key scopes narrower than "read this project", and per-key limits (Enterprise row, 0017).
- Invitation links and email (06d).

## Constraints

- Library first: `crypto/rand`, `crypto/sha256`, `crypto/subtle`, `encoding/base32` and `encoding/base64` from the standard library. No new dependency.
- CLAUDE.md and decision 0027:
  - Never log a key secret or the full key.
  - Every `/api/v1` route goes through the one gate and the role table.
  - Compare in constant time.
- Every new response validates against `docs/api/openapi.yaml` in the tests. `docs/api/readers.json` lists the console readers of the new operations.
- The console uses shadcn components, the existing `CopyCommand` and the Dialog from 06b's member removal.
- Real-thing tests: Postgres through testcontainers, `httptest`, Vitest with Testing Library.

## Acceptance Checks

1. Given an admin of organisation A, when they create a key named "ci" for project p1:
   - The answer is 201 with a secret matching `^vgk_[a-z2-7]{12}_[A-Za-z0-9_-]{43}$`.
   - The stored row holds the SHA-256 of the secret and not the secret.
   - `listProjectKeys` then shows it with its prefix and no secret.
   - The secret appears in no log line.
   (Measured by `TestCreateProjectKey`.)
2. Given that key:
   - Reading `listCalls` and `getCall` for p1 answers 200.
   - Reading p2 in the same organisation answers 403. So does a project in organisation B.
   - `getMembers` and `listProjectKeys` answer 403.
   - `getWorkspaces` returns A with p1 only.
   (Measured by `TestProjectKeyReadsOnlyItsProject`.)
3. Given a revoked key, a key with one changed character, an unknown `key_id`, and a value that is not a key, then each answers 401 with an identical body. Over 20 tries each, the median times of the unknown `key_id` and the wrong secret differ by less than 25% (measured by `TestProjectKeyFailuresLookAlike`).
4. Given a key used 50 times within one minute, then `last_used_at` changes at most once, and it is set (measured by `TestProjectKeyLastUsedIsThrottled`).
5. Given a Viewer, a Member and an Admin of A:
   - `createProjectKey`, `revokeProjectKey` and `listProjectKeys` are 403 for the Viewer and the Member, and succeed for the Admin (3 operations × 3 roles = 9 cases).
   - A key id from p2, revoked through p1's path, is 404, and the p2 key still works.
   - Revoking the same key twice is 204 both times.
   (Measured by `TestProjectKeyRoles`.)
6. Given `VOICEGATEWAY_API_TOKEN` set to a valid old token:
   - web starts and logs exactly one warning that names the variable.
   - A request bearing that token is 401.
   - `config` no longer has the field.
   (Measured by `TestInterimTokenRetired`.)
7. Given `voicegateway key create` for an existing project, then it prints one key that reads that project (200), and `key revoke` with its prefix makes the key 401 (measured by `TestKeyCommands`).
8. Given each new operation, then every response in checks 1 to 5 validates against the contract (measured by the response validator in those tests).
9. Given the console with the mock off, the Vitest suite `api-keys.test.tsx` runs 5 cases (measured by that suite):
   - An Admin sees the keys of each project.
   - Creating a key shows its secret once, and closing the dialog removes it from the page.
   - Revoke asks first, and only the confirmation calls `revokeProjectKey`.
   - A Viewer sees that admins manage keys, with no list.
   - A 404 shows "Not served by this server yet".
10. Given the log output of checks 1 to 7, then no line contains a key secret, a full key, a password or a session id (measured by `TestNoSecretInLogs`, extended).

## Manual Check

On the Compose stack behind HTTPS:
1. Create a key in Settings, Access, API keys.
2. Run `curl -H "Authorization: Bearer <key>"` on the project's `/calls` and get 200, then on another project and get 403.
3. Revoke the key and get 401.
4. Start web with the old `VOICEGATEWAY_API_TOKEN` still set and see the one warning.

## Open Questions

These are my recommendations; change any before building.

- **Format:** `vgk_<key_id>_<secret>` with a separate lookup id, rather than looking up by the hash of the whole value. The visible prefix lets an admin tell keys apart, and lookup stays an indexed equality.
- **Who manages keys:** Admin only, for listing too (0019's "keys"). A key is access to a project's transcripts.
- **What a key reads:** a key is a read-only Viewer of one project. `getWorkspaces` shows its one project, so an MCP client can find its scope.
- **Last used:** `last_used_at` is updated at most once a minute, rather than on every request.
- **No expiry in 06c.** A key lives until revoked.
- **Retiring the token:** a token still set produces a warning, and web keeps running, rather than refusing to start. That way an upgrade with an old `.env` keeps working, and the token simply stops being accepted.
