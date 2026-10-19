#!/usr/bin/env bash
# VoiceGateway installer.
# Public URL:  https://voicegateway.dev/install.sh
# Run:         curl -fsSL https://voicegateway.dev/install.sh | bash
#
# Does step 1 of docs/self-hosting.md: checks for Docker with Compose v2, git and make, checks out
# the release into ./voicegateway, starts the six-service stack from the published image and runs
# the preflight check. Re-running it in the same place moves the checkout to the pinned release and
# starts the stack again; deploy/.env and the data volumes are kept.
#
#   VOICEGATEWAY_VERSION  release to install (default below)
#   VOICEGATEWAY_DIR      where the checkout goes (default ./voicegateway)
set -euo pipefail

DEFAULT_VERSION="1.0.0-preview.3"
REPO_URL="https://github.com/mahimailabs/voicegateway.git"
IMAGE="ghcr.io/mahimailabs/voicegateway"
GUIDE_URL="https://github.com/mahimailabs/voicegateway/blob/main/docs/self-hosting.md"

VERSION="${VOICEGATEWAY_VERSION:-$DEFAULT_VERSION}"
DIR="${VOICEGATEWAY_DIR:-voicegateway}"

die()  { printf '!! %s\n' "$*" >&2; exit 1; }
step() { printf '\n>> %s\n' "$*"; }

# A release is a semantic version, so it is safe in a git ref and an image tag.
check_version() {
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || die "VOICEGATEWAY_VERSION must look like 1.0.0 or 1.0.0-preview.3, got: $VERSION"
}

check_tools() {
    local missing=()
    for tool in docker git make; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    [ "${#missing[@]}" -eq 0 ] || die "Install ${missing[*]} first, then run this again. See $GUIDE_URL for what VoiceGateway needs."
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing: 'docker compose version' failed. Install the Docker Compose plugin, then run this again."
    docker info >/dev/null 2>&1 || die "Docker is installed but not running, or this user cannot reach it. Start Docker, then run this again."
}

# Clones the release, or moves an existing checkout of this repository to it.
checkout() {
    local tag="v$VERSION"
    local origin dirty
    if [ -d "$DIR/.git" ]; then
        origin="$(git -C "$DIR" remote get-url origin)" || die "Cannot read the checkout origin."
        case "$origin" in
            https://github.com/mahimailabs/voicegateway.git|https://github.com/mahimailabs/voicegateway|git@github.com:mahimailabs/voicegateway.git) ;;
            *) die "$DIR is not a VoiceGateway checkout from the official repository." ;;
        esac
        dirty="$(git -C "$DIR" status --porcelain --untracked-files=normal)" || die "Cannot inspect the checkout."
        [ -z "$dirty" ] || die "$DIR has local changes. Save them before running the installer again."
        step "Updating $DIR to $tag"
        git -C "$DIR" fetch --depth 1 origin tag "$tag"
        git -C "$DIR" checkout --quiet "$tag"
    elif [ -e "$DIR" ]; then
        die "$DIR already exists and is not a VoiceGateway checkout. Set VOICEGATEWAY_DIR to another path."
    else
        step "Downloading VoiceGateway $tag into $DIR"
        git -c advice.detachedHead=false clone --quiet --branch "$tag" --depth 1 "$REPO_URL" "$DIR"
    fi
}

start() {
    step "Starting the stack from $IMAGE:$VERSION"
    VOICEGATEWAY_IMAGE="$IMAGE:$VERSION" make -C "$DIR" up
    step "Checking that every service answers"
    make -C "$DIR" preflight
}

next_steps() {
    cat <<EOF

VoiceGateway $VERSION is running at http://localhost:8080.

Next, from $DIR:
  make user-add EMAIL=you@example.com     # the first admin
  make project-add NAME="Production"      # with LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET set

Then point your agent and LiveKit's webhooks at VoiceGateway: $GUIDE_URL
EOF
}

main() {
    check_version
    check_tools
    checkout
    start
    next_steps
}

# Sourcing the script (the tests do) defines the functions without running anything.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
