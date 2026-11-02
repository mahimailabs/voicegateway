#!/usr/bin/env bash
# Checks what docs/self-hosting.md promises once its steps 1 to 4 have run, the way a user would
# see it: the console answers, the first admin from step 2 signs in, the project from step 3 is
# there with the ingestion URLs from step 4, and /mcp asks for a project key. The Install test
# workflow runs it; it needs curl and jq, and reads the password from VOICEGATEWAY_USER_PASSWORD
# like make user-add does.
set -euo pipefail

BASE="${VOICEGATEWAY_URL:-http://localhost:8080}"
EMAIL="${VOICEGATEWAY_USER_EMAIL:-you@example.com}"
PROJECT="${VOICEGATEWAY_PROJECT:-production}"
: "${VOICEGATEWAY_USER_PASSWORD:?set VOICEGATEWAY_USER_PASSWORD to the password make user-add stored}"

fail() { printf '!! FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok: %s\n' "$*"; }

JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

curl -fsS --retry 20 --retry-delay 3 --retry-all-errors "$BASE/healthz" >/dev/null || fail "$BASE/healthz does not answer"
pass "the console answers at $BASE"

curl -fsS -o /dev/null "$BASE/" || fail "the console page does not load"
pass "the console page loads"

jq -n --arg email "$EMAIL" --arg password "$VOICEGATEWAY_USER_PASSWORD" '{email: $email, password: $password}' |
    curl -fsS -o /dev/null -c "$JAR" -H 'Content-Type: application/json' --data-binary @- "$BASE/auth/sign-in" ||
    fail "the first admin $EMAIL cannot sign in"
curl -fsS -o /dev/null -b "$JAR" "$BASE/auth/session" || fail "no session after signing in"
pass "the first admin signs in"

connection="$(curl -fsS -b "$JAR" "$BASE/api/v1/org/default/project/$PROJECT/connection")" ||
    fail "project $PROJECT from step 3 cannot be read"
pass "project $PROJECT is there"

want="$BASE/ingest/default/$PROJECT"
got="$(jq -r '.urls.observability' <<<"$connection")"
[ "$got" = "$want" ] || fail "the observability URL is $got, want $want (VOICEGATEWAY_PUBLIC_URL from step 4)"
got="$(jq -r '.urls.webhook' <<<"$connection")"
[ "$got" = "$want/livekit/webhook" ] || fail "the webhook URL is $got, want $want/livekit/webhook"
pass "the Connect your agent URLs use VOICEGATEWAY_PUBLIC_URL"

code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '{}' "$BASE/mcp")"
[ "$code" = 401 ] || fail "/mcp answered $code without a key, want 401"
pass "/mcp is served and asks for a project key"
