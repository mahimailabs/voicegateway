# CLAUDE.md

<!--
Rules Claude reads at the start of every session.
Keep this file under 80 lines. Put facts in PRODUCT.md, ARCHITECTURE.md and DESIGN.md, and keep only rules and pointers here.
Every TODO line must be replaced before the first build. Run tools/scripts/check-placeholders.sh to find them.
-->

## Project

TODO: One sentence on what this project is. Example: "A web app that lets small clinics book follow-up calls."

## Source of truth

Read these before any feature work. Do not restate their contents here.

- PRODUCT.md: who it is for, what it does, what is out of scope.
- ARCHITECTURE.md: components, boundaries, stack and the reasons for it.
- DESIGN.md: visual system. Only for UI work.
- docs/decisions/: why each major choice was made. Never reverse one without writing a new decision.
- docs/specs/: one file per phase. The current phase is the one named in the prompt.
- docs/progress.md: what is done, what is next, open questions.

## Stack

TODO: Language, framework, database, hosting, with pinned major versions. Example: "Python 3.12, FastAPI 0.115, PostgreSQL 16."

## Commands

TODO: Replace with the real commands.

- Install: TODO
- Run locally: TODO
- Test: TODO
- Lint and format: TODO

## Boundaries

TODO: The rules that keep the architecture intact. Example: "The web app never queries the database directly; it calls the API."

## Never do

- Never commit secrets or .env files.
- Never invent data, API responses or test results. If something is unknown, say so.
- Never mark a phase done until its acceptance checks pass.
- Never change behaviour outside the current spec without asking first.
- TODO: Add project-specific prohibitions.

## How to ask me

- When a choice is ambiguous, ask: "A or B? I recommend B because ...".
- Ask one question at a time and wait for the answer.
- If the spec contradicts PRODUCT.md or a decision record, stop and point at the contradiction.

## Working loop

1. Read the spec for this phase, plus the files above.
2. Build only what the spec asks for.
3. Run the acceptance checks in the spec and report each result with its number.
4. Update docs/progress.md. Add any new lasting rule to this file.
