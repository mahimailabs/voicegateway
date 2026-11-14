# 0042: Cloud plans count credits and may cap retention, projects and members

- Status: accepted
- Date: 2026-11-14
- Amends: 0024

## Context

The instance entitlement (phase 06e) gives an organisation a period and a number of profiled call minutes. Cloud plans need more than a minute count: a plan may keep call data for fewer days than the install's retention, and may cap how many projects and members an organisation has. Mahimai approved counting plans in credits on 2026-11-14.

## Decision

- An entitlement's allowance is `limitCredits`. One profiled call minute is one credit, so stored values carry over unchanged. `limitMinutes` is removed with no alias; the control plane sends `limitCredits`.
- Three optional limits join the entitlement: `retentionDays`, `maxProjects` and `maxMembers`. Absent or null is unlimited, and the API answers null, never zero.
- At zero credits left, new calls of that organisation are not profiled (the ingest refusal `plan_cap`, as before). Calls themselves are never blocked, and a call admitted before the cap still finishes. The console says profiling is paused and why.
- `retentionDays`: the worker trims, every hour, the organisation's closed calls that ended before the cutoff, with their summaries, participants and recording links, then its ClickHouse call data (events, spans, metric points, trace links, unmatched rows, room and participant reads). Billed usage stays.
- `maxProjects` and `maxMembers`: creating one more project, or inviting one more member, is 409 `plan_limit`. Members and open invitations both count. The check runs under the organisation's row lock, so parallel creates cannot both pass.
- A self-hosted install has no entitlement: nothing changes, and the console shows no plan.

## Consequences

- One Postgres migration (00026) renames `limit_minutes` to `limit_credits` and adds the three nullable columns.
- Lowering a limit below what an organisation already has refuses new projects and invitations only; nothing existing is removed.
- Recording audio objects of trimmed calls stay in the bucket until the bucket's own 90-day retention; their links go with the call, so nothing offers them.
- Prices stay out of the instance (0024): what a credit costs is the control plane's.
