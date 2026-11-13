# 0041: The instance admin role is open source

- Status: accepted
- Date: 2026-11-13
- Amends: 0017

## Context

0017 puts "Multiple organisations" and "Organisation and instance management API" in Enterprise. Until now the only way to see a whole instance was the instance token of phase 06e, which names no user and has no console pages.

On 2026-11-13 Mahimai asked for an admin who signs in to the same console as everyone, sees what every user sees and gets extra pages about the whole instance. He asked that no email address be hard coded, that someone self-hosting can create such an admin and sign in, and that this not sit in `ee/`.

## Options

1. **Keep 0017.** The role and its pages go in `ee/`. Cloud gets them; a free self-hosted install keeps only the one-organisation Admin.
2. **Move the instance admin role and its read-only pages to MIT.** Everything else in 0017's Enterprise column stays there.

## Decision

Option 2, chosen by Mahimai on 2026-11-13. The instance admin role, the command that grants it, its field on the session, the read-only admin operations (organisations, users, usage per organisation) and the console's Admin group are MIT, outside `ee/`. So is the setting that lets an operator add its own same-origin pages to that group.

## Consequences

- **0017's table changes in one row only.** Reading organisations, users and usage as a signed-in instance admin is free. Creating organisations over the API, the instance token, entitlements, SSO, SCIM, the audit log and the other Enterprise rows stay as 0017 says.
- **No address in configuration.** The role is a column on the account, set from the command line by someone with a shell on the install, the same trust that makes the first admin (0027). No HTTP route grants it.
- **The role is not an organisation role.** An instance admin reads counts and lists about the instance; it reads a project's calls only through a membership.
- **Operator pages stay out of this repository.** An operator, Cloud included, lists its own pages in configuration; they check the role through `GET /auth/session`. Billing and revenue stay in the private repository, as 0017 already says.
- **A free install with one organisation sees a short list.** The pages are still correct there, and they are ready when an install has more.
