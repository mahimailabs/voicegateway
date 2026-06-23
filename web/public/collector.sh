#!/usr/bin/env bash
# VoiceGateway collector installer.
# Public URL:  https://voicegateway.mahimai.ca/collector.sh
# Run:         curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash
#
# Stands up the fleet collector (SQLite or Postgres) with Docker: persists
# secrets, pins the image, health-checks, and helps expose it over HTTPS.
# Idempotent: re-running reuses the existing deploy and secrets.
set -euo pipefail

REPO="mahimailabs/voicegateway"
IMAGE="mahimairaja/voicegateway"

BACKEND="${VOICEGW_BACKEND:-}"
# shellcheck disable=SC2034  # DOMAIN used in future reverse-proxy step
DOMAIN="${VOICEGW_DOMAIN:-}"
DIR="${VOICEGW_DIR:-/opt/voicegateway}"
VERSION="${VOICEGW_VERSION:-}"
ASSUME_YES="${VOICEGW_ASSUME_YES:-0}"
BIND=""
INGEST_KEY=""
PG_PASS=""

say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf '!! %s\n' "$*" >&2; exit 1; }
step() { printf '\n>> %s\n' "$*"; }

# Yes/no confirm. Reads /dev/tty so it works under curl|bash. With
# ASSUME_YES=1, returns yes without prompting.
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local reply
    # shellcheck disable=SC2086
    printf '%s [y/N] ' "$1" > /dev/tty
    # shellcheck disable=SC2162
    read -r reply < /dev/tty || return 1
    case "$reply" in [yY]|[yY][eE][sS]) return 0;; *) return 1;; esac
}

usage() {
    cat <<'EOF'
VoiceGateway collector installer (collector.sh)

  curl -fsSL https://voicegateway.mahimai.ca/collector.sh | bash

Options (also settable via VOICEGW_* env vars):
  --sqlite | --postgres   backend (default: prompt)
  --domain <d>            expose via reverse proxy at collector.<d> (default: none)
  --dir <path>            deploy dir (default: /opt/voicegateway)
  --version <x>           image version (default: latest release)
  --yes                   assume yes to prompts (non-interactive)
  --help                  show this help
EOF
}

parse_args() {
    # Re-seed from env on each call so tests can override via env prefix.
    BACKEND="${VOICEGW_BACKEND:-${BACKEND:-}}"
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --sqlite) BACKEND=sqlite;;
            --postgres) BACKEND=postgres;;
            --domain) DOMAIN="$2"; shift;;
            --dir) DIR="$2"; shift;;
            --version) VERSION="$2"; shift;;
            --yes) ASSUME_YES=1;;
            --help|-h) usage; exit 0;;
            *) die "unknown option: $1 (try --help)";;
        esac
        shift
    done
}

detect_os() {
    [ "$(uname -s)" = "Linux" ] || die "collector.sh supports Linux servers only. See https://voicegateway.mahimai.ca/docs/deployment"
}

resolve_version() {
    [ -n "$VERSION" ] && return 0
    local tag
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    tag="${tag#v}"
    [ -n "$tag" ] || die "could not resolve the latest version; pass --version <x>"
    VERSION="$tag"
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        return 0
    fi
    warn "Docker (with the compose plugin) is required."
    confirm "Install Docker now via get.docker.com?" || die "install Docker, then re-run"
    curl -fsSL https://get.docker.com | sh
    command -v docker >/dev/null 2>&1 || die "Docker install failed"
}

load_or_make_secrets() {
    if [ -z "$INGEST_KEY" ] && [ -f "$DIR/voicegw.yaml" ]; then
        INGEST_KEY="$(sed -n 's/.*token: *"\(.*\)".*/\1/p' "$DIR/voicegw.yaml" | head -1)"
    fi
    [ -n "$INGEST_KEY" ] || INGEST_KEY="$(openssl rand -hex 32)"
    if [ -z "$PG_PASS" ] && [ -f "$DIR/.env" ]; then
        PG_PASS="$(sed -n 's/^VOICEGW_PG_PASSWORD=//p' "$DIR/.env" | head -1)"
    fi
    [ -n "$PG_PASS" ] || PG_PASS="$(openssl rand -hex 24)"
}

write_config() {
    mkdir -p "$DIR"
    cat > "$DIR/voicegw.yaml" <<EOF
auth:
  api_keys:
    - token: "$INGEST_KEY"
      name: fleet-agents
      scopes: [write]
EOF
    if [ "$BACKEND" = postgres ]; then
        printf 'VOICEGW_PG_PASSWORD=%s\n' "$PG_PASS" > "$DIR/.env"
        cat > "$DIR/docker-compose.yml" <<EOF
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: voicegw
      POSTGRES_PASSWORD: \${VOICEGW_PG_PASSWORD}
      POSTGRES_DB: voicegw
    volumes: [voicegw-pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U voicegw -d voicegw"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped
  collector:
    image: $IMAGE:$VERSION
    ports: ["$BIND:8080"]
    environment:
      VOICEGW_DB_URL: postgresql+asyncpg://voicegw:\${VOICEGW_PG_PASSWORD}@postgres:5432/voicegw
      VOICEGW_CONFIG: /app/voicegw.yaml
    volumes: ["./voicegw.yaml:/app/voicegw.yaml:ro"]
    depends_on:
      postgres: {condition: service_healthy}
    restart: unless-stopped
volumes:
  voicegw-pgdata:
EOF
    else
        cat > "$DIR/docker-compose.yml" <<EOF
services:
  collector:
    image: $IMAGE:$VERSION
    ports: ["$BIND:8080"]
    environment:
      VOICEGW_DB_PATH: /data/voicegw.db
      VOICEGW_CONFIG: /app/voicegw.yaml
    volumes:
      - "./voicegw.yaml:/app/voicegw.yaml:ro"
      - "voicegw-data:/data"
    restart: unless-stopped
volumes:
  voicegw-data:
EOF
    fi
}

scaffold() { load_or_make_secrets; write_config; }

main() {
    parse_args "$@"
    detect_os
    resolve_version
    ensure_docker
    step "Scaffolding $DIR"
    scaffold
    step "Starting collector"
    docker compose -f "$DIR/docker-compose.yml" up -d
    say "Collector is up."
    say "Ingest token: $INGEST_KEY"
    say "Deploy dir:   $DIR"
    [ -n "$DOMAIN" ] && say "Domain:       $DOMAIN"
    :
}

# Source-guard: run main only when executed, not when sourced by tests.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
