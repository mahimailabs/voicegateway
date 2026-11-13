# Instance admin

## Goal

One account can be an instance admin: it signs in to the same console as everyone, sees what every user sees, and gets an Admin group in the menu with pages about the whole instance. No email address is named in configuration or code. An operator can add its own pages to that group without changing this repository.

## Context

- Decision 0027: sign-in, sessions and the one access gate on every `/api/v1` route. Decision 0019: organisation roles Viewer, Member, Admin.
- Decision 0028 and `docs/specs/phase06e-instance-api.md`: the `instance` scope, reached today only by the instance token, which names no user.
- Decision 0017 puts multiple organisations and instance management in Enterprise. Where this phase's code lives (MIT or `ee/`) is set by the decision that accompanies this spec, not by this text.
- `internal/store/store.go` `Principal`; `internal/auth/auth.go` `session`; `internal/api/roles.go` `routeAccess`, `handle`; `cmd/voicegateway/main.go` `userCommand`.
- `dashboard/src/components/app-shell.tsx` `groups` (the menu) and `dashboard/src/router.tsx`.

## In Scope

- **The role.** A boolean on the account, `users.instance_admin`, false by default (one Postgres migration). It is not an organisation role and grants no organisation role: an instance admin reads a project only through a membership, as today.
- **Granting it.** `voicegateway user add --instance-admin` sets it on a new or existing account. `voicegateway user set-instance-admin --email <e> --off` removes it. There is no HTTP route that grants or removes it, so a stolen session cannot make an admin. Removing the last instance admin is allowed: the command line can always make another.
- **The principal.** `store.Principal` gains `InstanceAdmin`, loaded on every request with the rest of the principal, so a change applies on the next request.
- **The session.** `GET /auth/session` adds `instanceAdmin` (boolean). Another program on the same host can forward a browser's session cookie to this route and read it; that is the supported way for an operator's own pages to check the role.
- **A new minimum role, `InstanceAdmin`,** on the `instance` scope in `routeAccess`. A session whose account is an instance admin may call routes at that role. The instance token may call them too. Everyone else gets 403 `forbidden`; no credential gets 401. Existing `Instance` routes stay token only.
- **Read operations** at `InstanceAdmin`, in `docs/api/openapi.yaml` with `x-access` and `x-source`:
  - `listInstanceOrganisations` `GET /api/v1/instance/organisations`: each organisation's id, name, creation time, member, admin and project counts, newest first, paged.
  - `listInstanceUsers` `GET /api/v1/instance/users`: each account's id, email, creation time, whether it is an instance admin, and its organisations with roles, newest first, paged. Never a password hash, session or token.
  - Usage per organisation reuses `getOrganisationUsage`, opened to `InstanceAdmin`.
- **The console.** For an instance admin the menu shows an Admin group with Organisations, Users and Usage, built on `data-table.tsx`. Other accounts never see the group, and its routes answer the console's not-found page for them. Unknown values stay unknown, never zero.
- **Extra admin pages.** `VOICEGATEWAY_ADMIN_PAGES`, a comma-separated list of `Label=/path`. Each path must be a same-origin absolute path. `GET /auth/client-config` returns them only to an instance admin. The console adds each to the Admin group and shows it inside the shell in a frame at `/admin/page/<slug>`. This repository names no page; the pages check the role themselves through `GET /auth/session`.

## Out of Scope

- Writing from the admin pages: suspending or deleting an organisation, editing a user, impersonation.
- Granting the role in the console.
- Any billing, revenue or payment page. Nothing in this repository names them.
- Audit log.

## Constraints

- No new library. Every new route goes through `handle`.
- A frame only ever loads a path from `VOICEGATEWAY_ADMIN_PAGES`; a path with a scheme, a host, `//` or `..` fails configuration at start.
- Console pages keep `frame-ancestors 'self'`, so a framed page on the same origin works and no other origin can frame the console.

## Done when

- `TestEveryRouteHasAMinimumRole` covers the new routes and role.
- On real Postgres: a member gets 403 on each new route, an instance admin 200, and the flag set by the command line applies on the next request without a new sign-in.
- `GET /auth/session` shows `instanceAdmin` true only for a flagged account.
- Console tests: the Admin group shows only for an instance admin; a configured page appears in the group and loads in the frame; a bad `VOICEGATEWAY_ADMIN_PAGES` value stops start-up with a message that names the variable.
