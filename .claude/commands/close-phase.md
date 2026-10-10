---
description: Record a finished phase and prepare it for review
argument-hint: <path to spec>
---

Close the phase described in $ARGUMENTS.

1. Re-run the acceptance checks in the spec. If any fails, stop and report it; do not continue.
2. Update docs/progress.md: move this phase to Done, set the next phase, and record open questions and known issues.
3. If this phase created a lasting rule, add it to CLAUDE.md. If it made a lasting choice, write a record in docs/decisions/ from the template.
4. Run tools/scripts/check-placeholders.sh and report any TODO: left in files this phase touched.
5. Create a branch named feat/, fix/ or chore/ plus the spec's short name, commit with a Conventional Commits message that names the phase, push, and open a pull request whose description lists each acceptance check and its result. No AI attribution in the commit or PR.
