#!/usr/bin/env bash
# Test stub for pipx. Mounted at /usr/local/bin/pipx inside container
# tests of install.sh so the installer never reaches PyPI for the
# (not-yet-published) v0.1.0 voicegateway package. Subcommands handled:
#
#   --version   prints a fixed banner so install.sh's smoke check passes
#   ensurepath  no-op (PATH is already set up correctly in the test image)
#   list        prints empty output (no existing voicegateway install)
#   install     records the call and exits 0
#   upgrade     records the call and exits 0
#   <other>     records the call and exits 0
#
# Output is plain text on stdout so the parent test runner can grep for
# the [stub] markers if it wants to assert on the call.

case "${1:-}" in
    --version)
        echo "1.99.0-vg-test-stub"
        ;;
    ensurepath)
        echo "[stub] pipx ensurepath"
        ;;
    list)
        # Always report an empty install list so install.sh takes the
        # "fresh install" branch in detect_existing_voicegw.
        echo ""
        ;;
    install)
        shift
        echo "[stub] pipx install $*"
        ;;
    upgrade)
        shift
        echo "[stub] pipx upgrade $*"
        ;;
    *)
        echo "[stub] pipx $*"
        ;;
esac
