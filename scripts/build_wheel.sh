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
