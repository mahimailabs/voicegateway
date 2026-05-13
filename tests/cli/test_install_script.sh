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
#   bash tests/cli/test_install_script.sh                              # all images, pipx
#   bash tests/cli/test_install_script.sh --image ubuntu:24.04         # single image, pipx
#   bash tests/cli/test_install_script.sh --installer uv               # all images, uv backend
#   bash tests/cli/test_install_script.sh --image debian:12 --installer uv
#
# --installer chooses which stub is mounted inside the container:
#   pipx (default) — _pipx_stub.sh at /usr/local/bin/pipx; install.sh sees
#                    no uv on PATH and falls through to the pipx branch.
#   uv             — _uv_stub.sh at /usr/local/bin/uv; install.sh detects
#                    uv first and routes through `uv tool install`.
#
# Environment:
#   SKIP_IF_NO_DOCKER=1   exit 0 silently when Docker is unavailable
#                         (used by CI matrix entries that don't have
#                         a Linux runner with Docker, e.g. macOS jobs)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/install.sh"
PIPX_STUB="$REPO_ROOT/tests/cli/_pipx_stub.sh"
UV_STUB="$REPO_ROOT/tests/cli/_uv_stub.sh"
EXPECTED_NEXT_STEP="Run the wizard to configure your gateway"

if [ ! -f "$INSTALL_SCRIPT" ]; then
    printf 'install.sh not found at %s\n' "$INSTALL_SCRIPT" >&2
    exit 2
fi
if [ ! -f "$PIPX_STUB" ]; then
    printf 'pipx stub not found at %s\n' "$PIPX_STUB" >&2
    exit 2
fi
if [ ! -f "$UV_STUB" ]; then
    printf 'uv stub not found at %s\n' "$UV_STUB" >&2
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
INSTALLER="pipx"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --image)
            if [ -z "${2:-}" ]; then
                printf '%s\n' "--image requires an argument (e.g. --image ubuntu:24.04)" >&2
                exit 2
            fi
            filter="$2"
            shift 2
            ;;
        --installer)
            if [ -z "${2:-}" ]; then
                printf '%s\n' "--installer requires an argument (pipx or uv)" >&2
                exit 2
            fi
            case "$2" in
                pipx|uv) INSTALLER="$2" ;;
                *)
                    printf 'unknown installer: %s (expected pipx or uv)\n' "$2" >&2
                    exit 2
                    ;;
            esac
            shift 2
            ;;
        *)
            printf 'unknown argument: %s\n' "$1" >&2
            printf 'usage: %s [--image <image>] [--installer pipx|uv]\n' "$0" >&2
            exit 2
            ;;
    esac
done

case "$INSTALLER" in
    pipx)
        STUB_PATH="$PIPX_STUB"
        STUB_MOUNT="/usr/local/bin/pipx"
        INSTALL_MARKER="[stub] pipx install"
        ;;
    uv)
        STUB_PATH="$UV_STUB"
        STUB_MOUNT="/usr/local/bin/uv"
        INSTALL_MARKER="[stub] uv tool install"
        ;;
esac

run_one() {
    local image="$1"
    local bootstrap="$2"

    printf '\n==========================================================\n'
    printf ' Running install.sh in: %s (installer=%s)\n' "$image" "$INSTALLER"
    printf '==========================================================\n'

    local out exit_code=0
    out="$(docker run --rm \
        -v "$INSTALL_SCRIPT:/install.sh:ro" \
        -v "$STUB_PATH:$STUB_MOUNT:ro" \
        -e DEBIAN_FRONTEND=noninteractive \
        "$image" \
        bash -c "set -e; $bootstrap; bash /install.sh" 2>&1)" || exit_code=$?

    if [ "$exit_code" -ne 0 ]; then
        printf 'FAIL: install.sh exited %d in %s (installer=%s)\n' "$exit_code" "$image" "$INSTALLER" >&2
        printf -- '----- captured output -----\n%s\n----- end -----\n' "$out" >&2
        return 1
    fi

    if ! printf '%s\n' "$out" | grep -qF "$EXPECTED_NEXT_STEP"; then
        printf 'FAIL: expected next-step line not found in %s (installer=%s)\n' "$image" "$INSTALLER" >&2
        printf 'Expected substring: %s\n' "$EXPECTED_NEXT_STEP" >&2
        printf -- '----- captured output -----\n%s\n----- end -----\n' "$out" >&2
        return 1
    fi

    if ! printf '%s\n' "$out" | grep -qF "$INSTALL_MARKER"; then
        printf 'WARN: install marker %q was not seen in %s (installer=%s).\n' "$INSTALL_MARKER" "$image" "$INSTALLER" >&2
        printf 'This means install.sh probably skipped the install step. Investigate.\n' >&2
        return 1
    fi

    printf 'PASS: %s (installer=%s)\n' "$image" "$INSTALLER"
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
