#!/usr/bin/env bash
# Test stub for uv. Mounted at /usr/local/bin/uv inside container tests
# of install.sh so the installer never reaches PyPI for the
# (not-yet-published) v0.1.0 voicegateway package. Mirrors
# tests/cli/_pipx_stub.sh in shape and intent.
#
# Subcommands handled:
#
#   --version           prints a fixed banner so install.sh's smoke check passes
#   tool list           prints empty (forces the "fresh install" branch in
#                       detect_existing_voicegw)
#   tool install        records the call and exits 0
#   tool upgrade        records the call and exits 0
#   tool update-shell   no-op (PATH already set up correctly in the test image)
#   <other>             records the call and exits 0
#
# Output is plain text on stdout so the parent test runner can grep for
# the [stub] markers if it wants to assert on the call.

case "${1:-}" in
    --version)
        echo "uv 0.5.0-vg-test-stub"
        ;;
    tool)
        case "${2:-}" in
            list)
                # Empty output forces install.sh into the fresh-install branch.
                echo ""
                ;;
            install)
                shift 2
                echo "[stub] uv tool install $*"
                ;;
            upgrade)
                shift 2
                echo "[stub] uv tool upgrade $*"
                ;;
            update-shell)
                echo "[stub] uv tool update-shell"
                ;;
            *)
                sub="${2:-}"
                shift 2 2>/dev/null || true
                echo "[stub] uv tool $sub $*"
                ;;
        esac
        ;;
    *)
        echo "[stub] uv $*"
        ;;
esac
