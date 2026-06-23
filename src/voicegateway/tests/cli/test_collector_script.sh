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

# secrets are not world-readable: 0700 deploy dir, 0600 .env (portable stat)
getperm() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
[ "$(getperm "$DIR")" = 700 ] || fail "deploy dir not 0700 (got $(getperm "$DIR"))"
[ "$(getperm "$DIR/.env")" = 600 ] || fail ".env not 0600 (got $(getperm "$DIR/.env"))"
pass "secret file permissions"

# sqlite backend has no postgres service
DIR="$T/sqlite"; BACKEND=sqlite; INGEST_KEY=""; PG_PASS=""; scaffold
grep -q "postgres" "$DIR/docker-compose.yml" && fail "sqlite should have no postgres"
grep -q "VOICEGW_DB_PATH" "$DIR/docker-compose.yml" || fail "sqlite db path"
pass "sqlite template"

# sqlite scaffold is idempotent: key unchanged on second run
sqlite_key1="$(sed -n 's/.*token: *"\(.*\)".*/\1/p' "$DIR/voicegw.yaml" | head -1)"
INGEST_KEY=""; PG_PASS=""
scaffold
sqlite_key2="$(sed -n 's/.*token: *"\(.*\)".*/\1/p' "$DIR/voicegw.yaml" | head -1)"
[ -n "$sqlite_key1" ] && [ "$sqlite_key1" = "$sqlite_key2" ] || fail "sqlite ingest key changed across runs"
pass "sqlite scaffold idempotent"

printf '\nALL TASK-1-2-3 TESTS PASSED\n'

# ---------------------------------------------------------------------------
# Task 4: bring_up and wait_healthy
# ---------------------------------------------------------------------------

# wait_healthy: stub curl to return ok (HEALTH_RETRIES=1, HEALTH_SLEEP=0)
STUB2="$(mktemp -d)"
cat > "$STUB2/curl" <<'STUB'
#!/usr/bin/env bash
echo '{"status":"ok"}'
STUB
chmod +x "$STUB2/curl"
HEALTH_RETRIES=1 HEALTH_SLEEP=0 PATH="$STUB2:$PATH" wait_healthy && pass "wait_healthy ok" || fail "wait_healthy"

# wait_healthy: stub curl to always fail; verify it exits non-zero.
# Run in a subshell so die() does not abort the test suite.
STUB3="$(mktemp -d)"
cat > "$STUB3/curl" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$STUB3/curl"
( HEALTH_RETRIES=1 HEALTH_SLEEP=0 PATH="$STUB3:$PATH" wait_healthy 2>/dev/null ) && fail "wait_healthy should fail" || pass "wait_healthy timeout exits non-zero"

printf '\nALL TASK-4 TESTS PASSED\n'

# ---------------------------------------------------------------------------
# Task 5: set_bind, gather_inputs, proxy_snippet
# ---------------------------------------------------------------------------

# binding follows whether a domain is set
DOMAIN=""; BACKEND=sqlite; set_bind; [ "$BIND" = "0.0.0.0" ] || fail "bind no-domain (got '$BIND')"
DOMAIN=c.example.com; set_bind; [ "$BIND" = "127.0.0.1" ] || fail "bind domain (got '$BIND')"
pass "set_bind"

# snippet mentions the SNI route and the domain
out="$(DOMAIN=c.example.com proxy_snippet)"
printf '%s' "$out" | grep -q "collector.c.example.com" || fail "snippet domain"
printf '%s' "$out" | grep -q "localhost:8080" || fail "snippet upstream"
printf '%s' "$out" | grep -qi "sni" || fail "snippet sni route"
pass "proxy_snippet"

printf '\nALL TASK-5 TESTS PASSED\n'

# ---------------------------------------------------------------------------
# Task 6: collector_url and print_summary
# ---------------------------------------------------------------------------

# collector_url: with domain
out="$(DOMAIN=c.example.com collector_url)"
[ "$out" = "https://collector.c.example.com" ] || fail "collector_url with domain (got '$out')"
pass "collector_url with domain"

# collector_url: without domain
out="$(DOMAIN="" collector_url)"
[ "$out" = "http://<this-host>:8080" ] || fail "collector_url no domain (got '$out')"
pass "collector_url no domain"

# print_summary: stdout contains URL, agent snippet (but NOT the ingest key, which goes to /dev/tty)
# Redirect /dev/tty to a temp file so we can capture the key line too.
SUMTTY="$(mktemp)"
out="$(DOMAIN=c.example.com INGEST_KEY=abc123 DIR=/tmp/d print_summary 2>/dev/null >/dev/stdout 3>"$SUMTTY" || true)"
# Capture both stdout and the tty output for key check
out_full="$(DOMAIN=c.example.com INGEST_KEY=abc123 DIR=/tmp/d print_summary 2>&1 || true)"
printf '%s' "$out_full" | grep -q "https://collector.c.example.com" || fail "summary url"
printf '%s' "$out_full" | grep -q "abc123" || fail "summary key"
printf '%s' "$out_full" | grep -q "VoiceGatewayObserver" || fail "summary snippet"
pass "print_summary"

printf '\nALL TASK-6 TESTS PASSED\n'
printf '\nPASSED\n'
