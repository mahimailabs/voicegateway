#!/usr/bin/env bash
# Host unit tests for web/public/collector.sh. Sources the script (the
# source-guard keeps main() from running) and calls functions directly,
# with curl/docker stubbed on PATH. No real Docker needed.
set -euo pipefail
DIR_SELF="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DIR_SELF/../../../.." && pwd)"
SCRIPT="$REPO_ROOT/web/public/collector.sh"
fail() { printf '!! FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok: %s\n' "$*"; }

# shellcheck disable=SC1090
source "$SCRIPT"

# parse_args reads flags and env
parse_args --postgres --domain c.example.com --dir /tmp/x --version 9.9.9 --yes
[ "$BACKEND" = postgres ] || fail "BACKEND flag"
[ "$DOMAIN" = c.example.com ] || fail "DOMAIN flag"
[ "$DIR" = /tmp/x ] || fail "DIR flag"
[ "$VERSION" = 9.9.9 ] || fail "VERSION flag"
[ "$ASSUME_YES" = 1 ] || fail "ASSUME_YES flag"
pass "parse_args flags"

VOICEGW_BACKEND=sqlite parse_args
[ "$BACKEND" = sqlite ] || fail "BACKEND env"
pass "parse_args env"

# usage exits 0 and mentions the URL
out="$(usage)"; printf '%s' "$out" | grep -q "collector.sh" || fail "usage text"
pass "usage"

# resolve_version honors --version
VERSION=1.2.3; resolve_version; [ "$VERSION" = 1.2.3 ] || fail "resolve_version override"
pass "resolve_version override"

# resolve_version reads the GitHub tag when unset (stub curl)
STUBBIN="$(mktemp -d)"
cat > "$STUBBIN/curl" <<'STUB'
#!/usr/bin/env bash
echo '{"tag_name": "v0.9.2"}'
STUB
chmod +x "$STUBBIN/curl"
VERSION=""; PATH="$STUBBIN:$PATH" resolve_version
[ "$VERSION" = 0.9.2 ] || fail "resolve_version from tag (got '$VERSION')"
pass "resolve_version from tag"

# scaffold is idempotent: secrets are generated once and reused
T="$(mktemp -d)"; DIR="$T/deploy"; BACKEND=postgres; VERSION=0.9.2; BIND="127.0.0.1:8080"
scaffold
k1="$(sed -n 's/.*token: *"\(.*\)".*/\1/p' "$DIR/voicegw.yaml" | head -1)"
p1="$(sed -n 's/^VOICEGW_PG_PASSWORD=//p' "$DIR/.env" | head -1)"
scaffold   # run again
k2="$(sed -n 's/.*token: *"\(.*\)".*/\1/p' "$DIR/voicegw.yaml" | head -1)"
p2="$(sed -n 's/^VOICEGW_PG_PASSWORD=//p' "$DIR/.env" | head -1)"
[ -n "$k1" ] && [ "$k1" = "$k2" ] || fail "ingest key changed across runs"
[ -n "$p1" ] && [ "$p1" = "$p2" ] || fail "pg password changed across runs"
case "$k1" in vk_*) fail "ingest key must not start with vk_";; esac
grep -q "$IMAGE:0.9.2" "$DIR/docker-compose.yml" || fail "image not pinned"
grep -q ":latest" "$DIR/docker-compose.yml" && fail "found :latest"
grep -q "postgresql+asyncpg://" "$DIR/docker-compose.yml" || fail "postgres scheme"
grep -q "127.0.0.1:8080:8080" "$DIR/docker-compose.yml" || fail "bind"
pass "scaffold idempotent + pinned + scheme + bind"

# sqlite backend has no postgres service
DIR="$T/sqlite"; BACKEND=sqlite; INGEST_KEY=""; PG_PASS=""; scaffold
grep -q "postgres" "$DIR/docker-compose.yml" && fail "sqlite should have no postgres"
grep -q "VOICEGW_DB_PATH" "$DIR/docker-compose.yml" || fail "sqlite db path"
pass "sqlite template"

printf '\nALL TASK-1-2-3 TESTS PASSED\n'
