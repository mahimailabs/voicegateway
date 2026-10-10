#!/usr/bin/env sh
# Lists every unfilled TODO: placeholder in the agent-facing docs.
# Exits 1 if any remain, so it can gate CI or a pre-commit hook.
# Run from the repository root.

files="CLAUDE.md AGENTS.md PRODUCT.md ARCHITECTURE.md DESIGN.md docs/progress.md docs/decisions"

matches=$(grep -rn "TODO:" $files 2>/dev/null \
  | grep -v "docs/decisions/0000-template.md")

if [ -n "$matches" ]; then
  echo "$matches"
  echo
  echo "$(echo "$matches" | wc -l | tr -d ' ') placeholder(s) left to fill."
  exit 1
fi

echo "No placeholders left."
