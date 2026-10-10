# Progress

<!--
Updated at the end of every phase, by hand or by /close-phase.
A fresh session reads this to know where the project stands.
-->

## Current Phase

V1 public launch, targeted for the end of October 2026. Work runs one step at a time:

1. License: done.
2. AI coding setup (this file, PRODUCT.md, ARCHITECTURE.md, DESIGN.md, CLAUDE.md, commands): in review.
3. Lean redesign of the dashboard: next.

## Done

| Phase | Spec | Result | Date |
| --- | --- | --- | --- |
| Open-core license | docs/decisions/0001-open-core-license.md | MIT core plus `ee/`, PR #313 | 2026-10-10 |

## Next

- Redesign the dashboard onto the brand system in DESIGN.md, Apple-like and lean: keep the features that are fully valuable to humans, trim or remove the rest. Draft it with `/new-spec` before building.
- Keep the MCP server on the same query paths as the dashboard so agents and humans see the same numbers.

## Open Questions

- Which features stay in V1 and which are trimmed in the redesign.
- V1 numeric targets: overhead `attach()` adds per turn, dashboard first load, reconciliation error against invoices (PRODUCT.md proposes 1 percent).
- Which features, if any, belong in `ee/`, and the legal entity named in the license.

## Known Issues

- The dashboard (`src/dashboard/frontend/`) still uses the older teal palette while the docs and landing page use `site/theme.css`.
