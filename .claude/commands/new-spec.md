---
description: Draft the spec for the next phase from the template
argument-hint: <phase number> <short name and goal>
---

Draft a new phase spec for: $ARGUMENTS

1. Read CLAUDE.md, PRODUCT.md, ARCHITECTURE.md, docs/progress.md and docs/specs/_template.md. Read DESIGN.md only if this phase touches the dashboard, docs or landing page.
2. Ask me up to three questions, one at a time, about anything the goal leaves unclear.
3. Write docs/specs/phaseNN-short-name.md from the template. Keep it buildable in one session; if it is not, propose a split instead.
4. Every acceptance check must have a condition, a number, and how it is measured (a pytest test, a CLI command, a measured page). Mark any check you could not quantify with TODO: and tell me.
5. Do not write any code.
