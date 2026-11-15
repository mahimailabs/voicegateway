# Instance admin

## Goal

One account can be an instance admin: it signs in to the same console as everyone, sees what every user sees, and gets an Instance group in Settings with pages about the whole instance. No email address is named in configuration or code. An operator can add its own pages to the top of that account's menu without changing this repository.

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
- **A new scope, `instance_admin`,** in `routeAccess`, with the minimum role `InstanceAdmin`. Only a session whose account has the flag may call its routes. A project key, an embed token, the instance token and every other account get 403 `forbidden`; no credential gets 401. Existing `instance` routes stay token only.
- **Read operations** at `InstanceAdmin`, under `/api/v1/admin`, in `docs/api/openapi.yaml` with `x-access` and `x-source`:
  - `listAdminOrganisations` `GET /api/v1/admin/organisations`: the newest 500 organisations, newest first, each with its id, name, creation time and member, admin and project counts, plus `total`, the count of all of them.
  - `listAdminUsers` `GET /api/v1/admin/users`: the newest 500 accounts, newest first, each with its id, email, creation time, whether it is an instance admin, and its organisations with roles, plus `total`. Never a password hash, session or token.
  - `getAdminOrganisationUsage` `GET /api/v1/admin/organisations/{orgId}/usage?from=&to=`: the same read, rules and limits as `getOrganisationUsage`.
- **The console.** For an instance admin the Settings modal shows an Instance group with Organisations and Users (`?settings=instance-organisations`, `?settings=instance-users`), built on `data-table.tsx`. Opening an organisation's row reads its call time for the last 30 days. Other accounts never see the group and read nothing from these routes; an address that names one of its pages opens Settings on its first page. Unknown values stay unknown, never zero. The menu keeps the pages people work in; lists and setup stay in Settings.
- **Extra admin pages.** `VOICEGATEWAY_ADMIN_PAGES`, a comma-separated list of `Label=/path`, at most 8. Each path must be an absolute path on this site. `GET /auth/session` returns them as `adminPages`, to an instance admin only. The console puts each at the top of the menu, above Overview, and shows it inside the shell in a frame at `/admin/page/<n>`, `n` its place in the list; any other account sees "No such page" there. This repository names no page; the pages check the role themselves through `GET /auth/session`.

## Out of Scope

- Writing from the admin pages: suspending or deleting an organisation, editing a user, impersonation.
- Granting the role in the console.
- Any billing, revenue or payment page. Nothing in this repository names them.
- Admin pages for an instance admin who belongs to no organisation: the shell still needs a project to open.
- Paging beyond the newest 500 organisations or accounts.
- Audit log.

## Constraints

- No new library. Every new route goes through `handle`.
- A frame only ever loads a path from `VOICEGATEWAY_ADMIN_PAGES`; a path with a scheme, a host, `//`, `..`, a query or a fragment, or one under `/api/` or `/auth/`, fails configuration at start.
- Console pages keep `frame-ancestors 'self'`, so a framed page on the same origin works and no other origin can frame the console.

## Done when

- `TestEveryRouteHasAMinimumRole` covers the new routes and role.
- On real Postgres: a member gets 403 on each new route, an instance admin 200, and the flag set by the command line applies on the next request without a new sign-in.
- `GET /auth/session` shows `instanceAdmin` true only for a flagged account.
- Console tests: Settings shows the Instance group only for an instance admin; a configured page loads in the frame; a bad `VOICEGATEWAY_ADMIN_PAGES` value stops start-up with a message that names the variable.
