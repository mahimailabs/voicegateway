#!/usr/bin/env bash
# tests/cli/test_install_script.sh
#
# Runs the repo-root install.sh inside fresh containers (Ubuntu LTS,
# Debian, Fedora) and asserts:
#
#   1. install.sh exits 0
#   2. The expected next-step line ("Run the wizard to configure your
#      gateway") appears in stdout, proving the script ran end-to-end
#      and reached its final guidance section
#
# The voicegateway[cloud,dashboard] PyPI install itself is stubbed via
# tests/cli/_pipx_stub.sh mounted at /usr/local/bin/pipx. This is
# necessary because v0.1.0 is not yet on PyPI; the test exercises the
# OS-detection, Python-detection, and end-of-script flow without
# fetching the (still-unpublished) package.
#
# Usage:
#   bash tests/cli/test_install_script.sh                 # run all images
#   bash tests/cli/test_install_script.sh --image ubuntu:24.04   # single
#
# Environment:
#   SKIP_IF_NO_DOCKER=1   exit 0 silently when Docker is unavailable
#                         (used by CI matrix entries that don't have
#                         a Linux runner with Docker, e.g. macOS jobs)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/install.sh"
PIPX_STUB="$REPO_ROOT/tests/cli/_pipx_stub.sh"
EXPECTED_NEXT_STEP="Run the wizard to configure your gateway"

if [ ! -f "$INSTALL_SCRIPT" ]; then
    printf 'install.sh not found at %s\n' "$INSTALL_SCRIPT" >&2
    exit 2
fi
if [ ! -f "$PIPX_STUB" ]; then
    printf 'pipx stub not found at %s\n' "$PIPX_STUB" >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    if [ "${SKIP_IF_NO_DOCKER:-0}" = "1" ]; then
        printf 'docker not found; SKIP_IF_NO_DOCKER=1, skipping.\n' >&2
        exit 0
    fi
    printf 'docker not found. Install Docker, or set SKIP_IF_NO_DOCKER=1 to skip.\n' >&2
    exit 2
fi

if ! docker info >/dev/null 2>&1; then
    if [ "${SKIP_IF_NO_DOCKER:-0}" = "1" ]; then
        printf 'docker daemon unavailable; SKIP_IF_NO_DOCKER=1, skipping.\n' >&2
        exit 0
    fi
    printf 'docker is installed but the daemon is not reachable.\n' >&2
    exit 2
fi

# image|python+ca-certificates bootstrap. The bootstrap command is
# evaluated inside the container before install.sh runs. It must yield
# a Python >= 3.11 binary on PATH (python3.11, python3.12, or python3).
IMAGES=(
    "ubuntu:24.04|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 ca-certificates >/dev/null"
    "debian:12|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 ca-certificates >/dev/null"
    "fedora:40|dnf install -y -q python3 ca-certificates >/dev/null"
)

filter=""
if [ "${1:-}" = "--image" ]; then
    if [ -z "${2:-}" ]; then
        printf '%s\n' "--image requires an argument (e.g. --image ubuntu:24.04)" >&2
        exit 2
    fi
    filter="$2"
fi

run_one() {
    local image="$1"
    local bootstrap="$2"

    printf '\n==========================================================\n'
    printf ' Running install.sh in: %s\n' "$image"
    printf '==========================================================\n'

    local out exit_code=0
    out="$(docker run --rm \
        -v "$INSTALL_SCRIPT:/install.sh:ro" \
        -v "$PIPX_STUB:/usr/local/bin/pipx:ro" \
        -e DEBIAN_FRONTEND=noninteractive \
        "$image" \
        bash -c "set -e; $bootstrap; bash /install.sh" 2>&1)" || exit_code=$?

    if [ "$exit_code" -ne 0 ]; then
        printf 'FAIL: install.sh exited %d in %s\n' "$exit_code" "$image" >&2
        printf -- '----- captured output -----\n%s\n----- end -----\n' "$out" >&2
        return 1
    fi

    if ! printf '%s\n' "$out" | grep -qF "$EXPECTED_NEXT_STEP"; then
        printf 'FAIL: expected next-step line not found in %s\n' "$image" >&2
        printf 'Expected substring: %s\n' "$EXPECTED_NEXT_STEP" >&2
        printf -- '----- captured output -----\n%s\n----- end -----\n' "$out" >&2
        return 1
    fi

    if ! printf '%s\n' "$out" | grep -qF "[stub] pipx install"; then
        printf 'WARN: stub pipx install was not invoked in %s.\n' "$image" >&2
        printf 'This means install.sh probably skipped the install step. Investigate.\n' >&2
        return 1
    fi

    printf 'PASS: %s\n' "$image"
    return 0
}

failures=0
total=0
for entry in "${IMAGES[@]}"; do
    image="${entry%%|*}"
    bootstrap="${entry#*|}"
    if [ -n "$filter" ] && [ "$filter" != "$image" ]; then
        continue
    fi
    total=$((total + 1))
    if ! run_one "$image" "$bootstrap"; then
        failures=$((failures + 1))
    fi
done

if [ "$total" = "0" ]; then
    printf 'No images matched filter %q. Available:\n' "$filter" >&2
    for entry in "${IMAGES[@]}"; do
        printf '  %s\n' "${entry%%|*}" >&2
    done
    exit 2
fi

printf '\n'
if [ "$failures" -gt 0 ]; then
    printf 'FAIL: %d of %d image(s) failed.\n' "$failures" "$total"
    exit 1
fi
printf 'PASS: %d of %d image(s) succeeded.\n' "$total" "$total"
