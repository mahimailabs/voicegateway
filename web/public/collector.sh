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
    printf '%s [y/N] ' "$1" > /dev/tty
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
            --domain) [ "$#" -ge 2 ] || die "--domain needs a value"; DOMAIN="$2"; shift;;
            --dir) [ "$#" -ge 2 ] || die "--dir needs a value"; DIR="$2"; shift;;
            --version) [ "$#" -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift;;
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
    # 0700 deploy dir is the protection boundary for the secrets it holds.
    # voicegw.yaml itself stays group/other-readable on purpose: the collector
    # container (uid 1000) reads it through the bind mount. The 0700 dir blocks
    # other host users from reaching it. The .env is locked to 0600 below.
    chmod 700 "$DIR"
    cat > "$DIR/voicegw.yaml" <<EOF
auth:
  api_keys:
    - token: "$INGEST_KEY"
      name: fleet-agents
      scopes: [write]
EOF
    if [ "$BACKEND" = postgres ]; then
        printf 'VOICEGW_PG_PASSWORD=%s\n' "$PG_PASS" > "$DIR/.env"
        chmod 600 "$DIR/.env"
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
    ports: ["$BIND:8080:8080"]
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
    ports: ["$BIND:8080:8080"]
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

# Task 4: bring up and health check

bring_up() {
    # Verify the daemon is reachable before attempting compose up.
    docker info >/dev/null 2>&1 || die "cannot reach the Docker daemon; try re-running with sudo, or add your user to the docker group and re-login"
    step "Starting the collector"
    ( cd "$DIR" && docker compose up -d )
}

wait_healthy() {
    local tries="${HEALTH_RETRIES:-30}" sleep_s="${HEALTH_SLEEP:-2}" i=0
    while [ "$i" -lt "$tries" ]; do
        if curl -fsS http://localhost:8080/health >/dev/null 2>&1; then
            say "collector is healthy"
            return 0
        fi
        i=$((i + 1)); sleep "$sleep_s"
    done
    die "collector did not become healthy. Check: cd $DIR && docker compose logs collector"
}

# Task 5: inputs, port binding, and exposure

set_bind() {
    if [ -n "$DOMAIN" ]; then
        BIND="127.0.0.1"
    else
        BIND="0.0.0.0"
    fi
}

gather_inputs() {
    if [ -z "$BACKEND" ]; then
        [ "$ASSUME_YES" = 1 ] && die "set --sqlite or --postgres in non-interactive mode"
        printf 'Backend? [1] SQLite (simple, single collector)  [2] Postgres (fleet): ' > /dev/tty
        local c; read -r c < /dev/tty
        case "$c" in 2) BACKEND=postgres;; *) BACKEND=sqlite;; esac
    fi
    set_bind
}

ports_free() { ! { ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null; } | grep -qE ':(80|443)[[:space:]]'; }

# Install Caddy on Debian/Ubuntu via the official apt repo. Returns non-zero
# if it cannot (non-apt distro or any failed step), so the caller falls back
# to printing the manual reverse-proxy snippet.
install_caddy() {
    command -v caddy >/dev/null 2>&1 && return 0
    command -v apt-get >/dev/null 2>&1 || return 1
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1 || return 1
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || return 1
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        > /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null || return 1
    apt-get update >/dev/null 2>&1 || return 1
    apt-get install -y caddy >/dev/null 2>&1
}

proxy_snippet() {
    cat <<EOF
# Caddy (caddyl4 / layer4 caddy.yaml) - add under apps.layer4.servers.main.routes,
# and add collector.$DOMAIN to apps.tls.certificates.automate:
          - match:
              - tls: { sni: ["collector.$DOMAIN"] }
            handle:
              - handler: tls
                connection_policies: [{ alpn: ["http/1.1"] }]
              - handler: proxy
                upstreams: [{ dial: ["localhost:8080"] }]

# nginx:
# server {
#   listen 443 ssl;
#   server_name collector.$DOMAIN;
#   location / { proxy_pass http://127.0.0.1:8080; }
# }
EOF
}

expose() {
    [ -z "$DOMAIN" ] && { say "Collector is on http://<this-host>:8080 (no domain given). See the docs to add HTTPS."; return 0; }
    if ports_free && confirm "Ports 80/443 are free. Install Caddy and serve https://collector.$DOMAIN?"; then
        if install_caddy \
            && printf 'collector.%s {\n    reverse_proxy localhost:8080\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile 2>/dev/null \
            && systemctl reload caddy 2>/dev/null; then
            say "Point collector.$DOMAIN at this host. The cert issues on first request."
            return 0
        fi
        warn "Caddy auto-setup did not complete; use the manual snippet below."
    fi
    step "Add this to your existing reverse proxy:"
    proxy_snippet
}

# Task 6: summary output

collector_url() {
    if [ -n "$DOMAIN" ]; then
        echo "https://collector.$DOMAIN"
    else
        echo "http://<this-host>:8080"
    fi
}

print_summary() {
    local url; url="$(collector_url)"

    # Show the ingest key on stderr, not stdout, so it stays out of a
    # `curl|bash > log` capture; under curl|bash it is still visible on the
    # terminal. It is also persisted in $DIR/voicegw.yaml.
    printf '\n!! SAVE THIS: ingest key (also in %s/voicegw.yaml):\n   %s\n' \
        "$DIR" "$INGEST_KEY" >&2

    cat <<EOF

>> Collector is up.
   URL:        $url
   Manage:     cd $DIR && docker compose logs -f   |   docker compose down

   Point your agents at it (use the ingest key shown above as api_key):

   from openrtc import AgentPool
   from voicegateway.openrtc import VoiceGatewayObserver
   pool = AgentPool(observers=[VoiceGatewayObserver(
       project="prod", collector_url="$url", api_key="<your-ingest-key>")])

   Note: only /v1/ingest and /health need to be public; protect the dashboard.
EOF
}

main() {
    parse_args "$@"
    detect_os
    ensure_docker
    resolve_version
    gather_inputs
    scaffold
    bring_up
    wait_healthy
    expose
    print_summary
}

# Source-guard: run main only when executed, not when sourced by tests.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
