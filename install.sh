#!/usr/bin/env bash
# VoiceGateway one-line installer (v0.1.0).
# Public URL:  https://voicegateway.mahimai.ca/install.sh
# Run:         curl -fsSL https://voicegateway.mahimai.ca/install.sh | bash
#
# Behaviour:
#  1. Detects OS (macOS / Linux / WSL). Refuses on anything else.
#  2. Verifies Python 3.11 or newer is installed. Refuses cleanly otherwise
#     with package-manager pointers (does NOT auto-install Python).
#  3. Installs pipx if missing (system package manager preferred,
#     pip --user fallback). Asks before sudo.
#  4. Detects an existing voicegateway install. If found, offers
#     pipx-upgrade plus migrate guidance. Otherwise pipx-installs
#     voicegateway[cloud,dashboard].
#  5. Prints the next step: voicegw onboard --install-daemon (or
#     voicegw migrate when upgrading from v0.0.5).
#
# Idempotent: re-running on an already-installed machine works.

set -euo pipefail

VG_PACKAGE_SPEC="voicegateway[cloud,dashboard]"
VG_PYPI_NAME="voicegateway"
PYTHON_REQUIRED_MAJOR=3
PYTHON_REQUIRED_MINOR=11

PYTHON_BIN=""
PYTHON_VERSION=""
EXISTING_VERSION=""
UPGRADED=0
OS=""

# ---------------------------------------------------------------------------
# Output helpers. Plain text. No em dashes per project convention.
# ---------------------------------------------------------------------------

say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf '!! %s\n' "$*" >&2; exit 1; }
step() { printf '\n>> %s\n' "$*"; }

# Confirm with the user. Returns 0 (yes) or 1 (no/timeout/no-tty).
# Always reads /dev/tty so it works under curl|bash pipes.
confirm() {
    local prompt="$1"
    local answer=""
    if [ -r /dev/tty ]; then
        # 60-second timeout so a non-attended pipe never hangs forever.
        if read -r -t 60 -p "$prompt [y/N]: " answer < /dev/tty; then
            :
        else
            return 1
        fi
    elif [ -t 0 ]; then
        if read -r -t 60 -p "$prompt [y/N]: " answer; then
            :
        else
            return 1
        fi
    else
        return 1
    fi
    case "$answer" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# OS detection.
# ---------------------------------------------------------------------------

detect_os() {
    local kernel
    kernel="$(uname -s)"
    case "$kernel" in
        Darwin)
            echo "macos"
            ;;
        Linux)
            if [ -r /proc/version ] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
                echo "wsl"
            else
                echo "linux"
            fi
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Python detection. Picks the newest Python >= 3.11 found on PATH.
# Sets PYTHON_BIN and PYTHON_VERSION on success.
# ---------------------------------------------------------------------------

check_python() {
    local candidate version major minor
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        version="$("$candidate" -c 'import sys; print("{0}.{1}".format(*sys.version_info[:2]))' 2>/dev/null || true)"
        if [ -z "$version" ]; then
            continue
        fi
        major="${version%%.*}"
        minor="${version##*.}"
        if [ "$major" -gt "$PYTHON_REQUIRED_MAJOR" ] ||
           { [ "$major" -eq "$PYTHON_REQUIRED_MAJOR" ] && [ "$minor" -ge "$PYTHON_REQUIRED_MINOR" ]; }; then
            PYTHON_BIN="$candidate"
            PYTHON_VERSION="$version"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# pipx bootstrap.
#
# Strategy by OS:
#   macOS:  brew install pipx (if brew present), else pip --user.
#   Linux:  apt/dnf when running as root or with sudo confirmation,
#           pip --user otherwise.
#   WSL:    same as Linux.
#
# Always finishes by ensuring ~/.local/bin is on PATH for the rest
# of this script.
# ---------------------------------------------------------------------------

ensure_pipx_path() {
    if [ -d "$HOME/.local/bin" ]; then
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ;;
            *) export PATH="$HOME/.local/bin:$PATH" ;;
        esac
    fi
}

ensure_pipx() {
    if command -v pipx >/dev/null 2>&1; then
        say "pipx already installed: $(pipx --version 2>/dev/null || echo unknown)"
        return 0
    fi

    say "pipx not found. Installing it now."

    case "$OS" in
        macos)
            if command -v brew >/dev/null 2>&1; then
                brew install pipx
                pipx ensurepath || true
            else
                "$PYTHON_BIN" -m pip install --user --upgrade pipx
                "$PYTHON_BIN" -m pipx ensurepath || true
            fi
            ;;
        linux|wsl)
            if [ "$(id -u)" = "0" ]; then
                if command -v apt-get >/dev/null 2>&1; then
                    apt-get update && apt-get install -y pipx
                    pipx ensurepath || true
                elif command -v dnf >/dev/null 2>&1; then
                    dnf install -y pipx
                    pipx ensurepath || true
                else
                    "$PYTHON_BIN" -m pip install --user --upgrade pipx
                    "$PYTHON_BIN" -m pipx ensurepath || true
                fi
            else
                "$PYTHON_BIN" -m pip install --user --upgrade pipx
                "$PYTHON_BIN" -m pipx ensurepath || true
            fi
            ;;
        *)
            "$PYTHON_BIN" -m pip install --user --upgrade pipx
            "$PYTHON_BIN" -m pipx ensurepath || true
            ;;
    esac

    ensure_pipx_path

    if ! command -v pipx >/dev/null 2>&1; then
        die "pipx install ran but pipx is not on PATH. Restart your shell and re-run, or add ~/.local/bin to your PATH manually."
    fi
    say "pipx installed: $(pipx --version 2>/dev/null || echo unknown)"
}

# ---------------------------------------------------------------------------
# Existing voicegateway detection.
#
# Returns 0 and sets EXISTING_VERSION when a previous install is found.
# Returns 1 otherwise.
# ---------------------------------------------------------------------------

detect_existing_voicegw() {
    if command -v pipx >/dev/null 2>&1; then
        local short
        short="$(pipx list --short 2>/dev/null || true)"
        if [ -n "$short" ] && printf '%s\n' "$short" | awk '{print $1}' | grep -qx "$VG_PYPI_NAME"; then
            EXISTING_VERSION="$(printf '%s\n' "$short" | awk -v p="$VG_PYPI_NAME" '$1==p{print $2; exit}')"
            return 0
        fi
    fi
    if command -v voicegw >/dev/null 2>&1; then
        EXISTING_VERSION="$(voicegw --version 2>/dev/null || echo unknown)"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

main() {
    say "VoiceGateway one-line installer (v0.1.0)."
    say "Install host: voicegateway.mahimai.ca."
    say "Docs host:    vg.openrtc.tech (different domain, intentional, see README)."

    OS="$(detect_os)"
    case "$OS" in
        macos)   say "OS detected: macOS." ;;
        linux)   say "OS detected: Linux." ;;
        wsl)     say "OS detected: WSL (Linux backend)." ;;
        unknown) die "Unsupported OS: $(uname -s). VoiceGateway supports macOS, Linux, and WSL on Windows." ;;
    esac

    step "Checking Python (>= ${PYTHON_REQUIRED_MAJOR}.${PYTHON_REQUIRED_MINOR} required)."
    if ! check_python; then
        die "Python ${PYTHON_REQUIRED_MAJOR}.${PYTHON_REQUIRED_MINOR}+ not found.

Install Python first, then re-run this installer:
  macOS:          brew install python@3.12
  Debian/Ubuntu:  sudo apt install python3.12 python3.12-venv
  Fedora/RHEL:    sudo dnf install python3.12

VoiceGateway does NOT auto-install Python. That choice is left to your OS package manager."
    fi
    say "Found $PYTHON_BIN (version $PYTHON_VERSION)."

    step "Ensuring pipx is installed."
    ensure_pipx

    step "Checking for an existing VoiceGateway install."
    if detect_existing_voicegw; then
        say "Found existing voicegateway: ${EXISTING_VERSION:-unknown}."
        if confirm "Upgrade to the latest release now?"; then
            pipx upgrade "$VG_PYPI_NAME" >/dev/null 2>&1 \
                || pipx install --force "$VG_PACKAGE_SPEC"
            UPGRADED=1
            say "Upgrade complete."
        else
            say "Skipping upgrade. Existing install left untouched."
        fi
    else
        step "Installing $VG_PACKAGE_SPEC via pipx."
        pipx install "$VG_PACKAGE_SPEC"
    fi

    step "Done."
    if [ "$UPGRADED" = "1" ]; then
        say "Bring v0.0.5 state into the v0.1.0 layout (idempotent, rollback-safe):"
        say "  voicegw migrate"
        say ""
        say "Then start (or restart) the daemon:"
        say "  voicegw restart"
    else
        say "Run the wizard to configure your gateway and register the daemon:"
        say "  voicegw onboard --install-daemon"
    fi
    say ""
    say "If anything looks off:"
    say "  voicegw doctor   (numbered punch list with fix steps)"
    say "  voicegw status   (daemon + provider status)"
}

main "$@"
