#!/usr/bin/env bash
# Build a PyPI wheel with the dashboard SPA bundled inside.
#
# Pipecat-prebuilt's pattern adapted to hatchling: build the React
# bundle, copy dist/ into the voicegateway package dir so hatchling
# picks it up as part of the wheel, then clean up the staged dir on
# exit (success, failure, or Ctrl-C alike).
#
# Usage:
#   bash scripts/build_wheel.sh
#
# Output:
#   dist/voicegateway-*.whl  (sdist + wheel via `uv build`)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FRONTEND_SRC="src/dashboard/frontend"
STAGED_DIST="src/voicegateway/_dashboard_dist"

if [[ ! -d "$FRONTEND_SRC" ]]; then
  echo "ERROR: $FRONTEND_SRC missing; this script must run from the repo root." >&2
  exit 1
fi

# Refuse to build without a lockfile: an unlocked install would resolve
# dependencies against whatever the registry returns today and the wheel
# would not be reproducible across runs.
if [[ ! -f "$FRONTEND_SRC/package-lock.json" && ! -f "$FRONTEND_SRC/npm-shrinkwrap.json" ]]; then
  echo "ERROR: no package-lock.json or npm-shrinkwrap.json in $FRONTEND_SRC; refusing to build a non-reproducible wheel." >&2
  exit 1
fi

echo "==> npm ci ($FRONTEND_SRC)"
npm --prefix "$FRONTEND_SRC" ci

echo "==> npm run build ($FRONTEND_SRC)"
npm --prefix "$FRONTEND_SRC" run build

if [[ ! -d "$FRONTEND_SRC/dist" ]]; then
  echo "ERROR: $FRONTEND_SRC/dist missing after build; npm build silently failed." >&2
  exit 1
fi

# Stage dist into the voicegateway package dir so hatchling picks it
# up via `packages = ["src/voicegateway"]`. The cleanup trap runs on
# ANY exit so the source tree never carries a stale _dashboard_dist
# across invocations.
rm -rf "$STAGED_DIST"
trap 'rm -rf "$STAGED_DIST"' EXIT
cp -r "$FRONTEND_SRC/dist" "$STAGED_DIST"

echo "==> uv build"
uv build

echo "==> built artifacts in dist/:"
ls -lh dist/
