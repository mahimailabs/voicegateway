#!/usr/bin/env bash
# Build a PyPI wheel with the dashboard SPA bundled inside.
#
# Pipecat-prebuilt's pattern adapted to hatchling: build the React
# bundle, copy dist/ into the voicegateway package dir so hatchling
# picks it up as part of the wheel, then clean up the staged dir on
# exit (success, failure, or Ctrl-C alike).
#
# Usage:
#   bash tools/scripts/build_wheel.sh
#
# Output:
#   dist/voicegateway-*.whl  (sdist + wheel via `uv build`)
set -euo pipefail

# Two levels up: this script lives at tools/scripts/, not at the repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Pin the version from the release tag. This script rebuilds the dashboard SPA
# into the source tree (below), which dirties the git working tree; hatch-vcs
# would then derive a PEP 440 local version like 0.10.0+g<hash>.d<date>, and
# PyPI rejects local versions with a 400. Pinning SETUPTOOLS_SCM_PRETEND_VERSION
# to the tag makes the wheel version exactly the release, immune to tree state,
# mirroring the Dockerfile (which pins it from its VERSION build arg). Outside a
# tagged CI run the var is left unset and hatch-vcs derives the version from git.
if [[ -z "${SETUPTOOLS_SCM_PRETEND_VERSION:-}" \
      && "${GITHUB_REF_TYPE:-}" == "tag" \
      && "${GITHUB_REF_NAME:-}" == v* ]]; then
  export SETUPTOOLS_SCM_PRETEND_VERSION="${GITHUB_REF_NAME#v}"
  echo "==> pinning version to ${SETUPTOOLS_SCM_PRETEND_VERSION} (from tag ${GITHUB_REF_NAME})"
fi

# Build one Vite SPA (dashboard or console) reproducibly, failing loudly if its
# lockfile or its dist is missing. Each SPA owns its own toolchain and lockfile
# (the console peers on React 19 + Tailwind v4, the dashboard on React 18).
build_spa() {
  local src="$1"
  if [[ ! -d "$src" ]]; then
    echo "ERROR: $src missing; this script must run from the repo root." >&2
    exit 1
  fi
  # Refuse to build without a lockfile: an unlocked install would resolve
  # dependencies against whatever the registry returns today and the wheel
  # would not be reproducible across runs.
  if [[ ! -f "$src/package-lock.json" && ! -f "$src/npm-shrinkwrap.json" ]]; then
    echo "ERROR: no package-lock.json or npm-shrinkwrap.json in $src; refusing to build a non-reproducible wheel." >&2
    exit 1
  fi
  echo "==> npm ci ($src)"
  npm --prefix "$src" ci
  echo "==> npm run build ($src)"
  npm --prefix "$src" run build
  if [[ ! -d "$src/dist" ]]; then
    echo "ERROR: $src/dist missing after build; npm build silently failed." >&2
    exit 1
  fi
}

FRONTEND_SRC="src/dashboard/frontend"
STAGED_DIST="src/voicegateway/_dashboard_dist"
CONSOLE_SRC="src/dashboard/console"
CONSOLE_STAGED_DIST="src/voicegateway/_console_dist"

build_spa "$FRONTEND_SRC"
build_spa "$CONSOLE_SRC"

# Stage both dists into the voicegateway package dir so hatchling picks them up
# via `packages = ["src/voicegateway"]`. ONE EXIT trap cleans both: a second
# `trap ... EXIT` would replace the first, leaking a staged dir. The trap runs on
# any exit so the source tree never carries a stale _dashboard_dist / _console_dist
# across invocations.
rm -rf "$STAGED_DIST" "$CONSOLE_STAGED_DIST"
trap 'rm -rf "$STAGED_DIST" "$CONSOLE_STAGED_DIST"' EXIT
cp -r "$FRONTEND_SRC/dist" "$STAGED_DIST"
cp -r "$CONSOLE_SRC/dist" "$CONSOLE_STAGED_DIST"

echo "==> uv build"
uv build

echo "==> built artifacts in dist/:"
ls -lh dist/
