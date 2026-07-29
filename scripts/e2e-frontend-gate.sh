#!/usr/bin/env bash
# G0 frontend verification gate (decision: Playwright-style behavioural
# assertions via gstack `browse`, not vitest/RTL, and no new npm dependency).
#
# Why this exists: `vite build --mode demo` passing proves the bundle compiles,
# NOT that anything rendered. The demo fixtures can disagree with what the
# backend actually returns and you still get a clean build with empty cards.
# This gate builds, serves the demo bundle, navigates, and asserts that the
# cards on a page contain real visible text.
#
# Usage:
#   scripts/e2e-frontend-gate.sh                  # gate the Diagnostics page
#   scripts/e2e-frontend-gate.sh calls            # gate another route
#   MIN_CARDS=5 scripts/e2e-frontend-gate.sh      # require at least 5 cards
#
# Exit 0 = gate green. Non-zero = gate red (message on stderr).
set -euo pipefail

ROUTE="${1:-diagnostics}"
MIN_CARDS="${MIN_CARDS:-1}"
MIN_CHARS="${MIN_CHARS:-20}"
PORT="${PORT:-4173}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$REPO/src/dashboard/frontend"

B="$REPO/.claude/skills/gstack/browse/dist/browse"
[ -x "$B" ] || B="$HOME/.claude/skills/gstack/browse/dist/browse"
if [ ! -x "$B" ]; then
  echo "gate: gstack browse binary not found; run its ./setup once" >&2
  exit 2
fi

echo "gate: building frontend (tsc + vite + demo)"
( cd "$FRONTEND" && npx tsc -b && npm run build && npm run build:demo ) >/dev/null

# The demo bundle is built with base '/demo/', so it must be served under that
# path or every asset 404s. Symlink rather than copy so a rebuild is picked up.
SERVE_ROOT="$(mktemp -d)"
ln -sfn "$FRONTEND/dist-demo" "$SERVE_ROOT/demo"

# A plain http.server has no SPA fallback, so a deep link like /demo/diagnostics
# 404s and the bundle never loads (the gate then fails for the wrong reason).
# Fall back to index.html for unknown paths, which is what the daemon does in
# production.
cat > "$SERVE_ROOT/serve.py" <<'PYSRV'
import http.server, os, sys

class SPAHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) and not self.path.startswith("/demo/assets/"):
            self.path = "/demo/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass

http.server.HTTPServer(("", int(sys.argv[1])), SPAHandler).serve_forever()
PYSRV

# A stale server from a previous run holds the port and serves a deleted root,
# which fails the next run with ERR_CONNECTION_RESET. Clear it first.
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "gate: port $PORT busy, clearing"
  lsof -ti:"$PORT" | xargs kill 2>/dev/null || true
  sleep 1
fi

echo "gate: serving $SERVE_ROOT on :$PORT (SPA fallback)"
# `exec` matters: without it, `&` backgrounds the whole `cd && python3` compound
# in an implicit subshell, so $! is that subshell and killing it orphans python3,
# which then holds the port forever. exec replaces the subshell with python3, so
# $! is the real server PID.
( cd "$SERVE_ROOT" && exec python3 serve.py "$PORT" ) >/dev/null 2>&1 &
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$SERVE_ROOT"
}
trap cleanup EXIT

for _ in $(seq 1 20); do
  curl -sf -o /dev/null "http://localhost:$PORT/demo/" && break
  sleep 0.25
done

# browse is a long-lived daemon and its console/network logs are CUMULATIVE
# across invocations, so a previous run's errors would fail this run. Clear both
# before navigating or the gate reports stale failures.
"$B" console --clear >/dev/null 2>&1 || true
"$B" network --clear >/dev/null 2>&1 || true

echo "gate: navigating to /demo/$ROUTE"
"$B" goto "http://localhost:$PORT/demo/$ROUTE" >/dev/null

mounted=$("$B" js "document.getElementById('root') ? document.getElementById('root').children.length : 0")
if [ "$mounted" = "0" ]; then
  echo "gate RED: #root has no children, the SPA did not mount" >&2
  exit 1
fi

# index.html references /static/branding/favicon.svg, which the daemon serves in
# production but is not part of dist-demo, so that one 404 is expected here and
# is not a render failure. Everything else is a real error.
# Match the log's own '[error]' marker, NOT a bare 'error' substring: the empty
# result prints the literal '(no console errors)', which a substring match counts
# as a failure.
errors=$("$B" console --errors 2>/dev/null \
  | grep -F "[error]" \
  | grep -v "static/branding" \
  | grep -c . || true)
if [ "${errors:-0}" -gt 0 ]; then
  echo "gate RED: console errors on /demo/$ROUTE" >&2
  "$B" console --errors | grep -v "static/branding" >&2
  exit 1
fi

# The real assertion: cards must carry visible text, not just exist.
report=$("$B" js "JSON.stringify(Array.from(document.querySelectorAll('.vg-card')).map(c => c.textContent.trim().length))")
echo "gate: card text lengths = $report"

count=$("$B" js "Array.from(document.querySelectorAll('.vg-card')).filter(c => c.textContent.trim().length >= $MIN_CHARS).length")
if [ "${count:-0}" -lt "$MIN_CARDS" ]; then
  echo "gate RED: only ${count:-0} card(s) with >=${MIN_CHARS} chars of text, need $MIN_CARDS" >&2
  echo "          (this is the 'builds clean but renders empty' failure mode)" >&2
  exit 1
fi

shot="${SHOT:-$REPO/.e2e-gate-$ROUTE.png}"
"$B" screenshot "$shot" >/dev/null
echo "gate GREEN: $count card(s) rendered with text on /demo/$ROUTE; screenshot $shot"
