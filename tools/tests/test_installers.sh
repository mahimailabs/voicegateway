#!/usr/bin/env bash
# Host tests for install.sh and the retired collector.sh. docker, git and make are stubs on PATH
# that log their arguments, so no Docker or network is needed. The real install runs in the
# Installers workflow's boot job.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fail() { printf '!! FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok: %s\n' "$*"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STUBS="$WORK/bin"
SYS="$WORK/sys"
LOG="$WORK/calls.log"
mkdir -p "$STUBS" "$SYS"
# Only the system tools the script and stubs use, so a missing tool can be tested on any host.
for tool in bash env cat mkdir; do ln -s "$(command -v "$tool")" "$SYS/$tool"; done

# Each stub logs "name args" and, for make, the image it was given. DOCKER_COMPOSE_FAILS and
# DOCKER_INFO_FAILS make the matching docker call fail; git clone creates the target's .git.
cat > "$STUBS/docker" <<'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$LOG"
[ "$1" = compose ] && [ -n "${DOCKER_COMPOSE_FAILS:-}" ] && exit 1
[ "$1" = info ] && [ -n "${DOCKER_INFO_FAILS:-}" ] && exit 1
exit 0
EOF
cat > "$STUBS/git" <<'EOF'
#!/usr/bin/env bash
printf 'git %s\n' "$*" >> "$LOG"
if [ "${3:-}" = remote ]; then printf '%s\n' "${TEST_ORIGIN:-https://github.com/mahimailabs/voicegateway.git}"; fi
if [ "${3:-}" = status ]; then printf '%s' "${TEST_DIRTY:-}"; fi
if [ "${3:-}" = clone ]; then mkdir -p "${!#}/.git"; fi
exit 0
EOF
cat > "$STUBS/make" <<'EOF'
#!/usr/bin/env bash
printf 'make %s image=%s\n' "$*" "${VOICEGATEWAY_IMAGE:-}" >> "$LOG"
exit 0
EOF
chmod +x "$STUBS"/*

# Runs install.sh in $WORK with only the stubs and $SYS on PATH.
run_install() {
    : > "$LOG"
    (cd "$WORK" && env -i HOME="$WORK" PATH="$STUBS:$SYS" LOG="$LOG" "$@" bash "$REPO_ROOT/install.sh") > "$WORK/out" 2>&1
}

rm -rf "$WORK/voicegateway"
run_install || fail "fresh install exited non-zero: $(cat "$WORK/out")"
grep -qx 'git -c advice.detachedHead=false clone --quiet --branch v1.0.0-preview.3 --depth 1 https://github.com/mahimailabs/voicegateway.git voicegateway' "$LOG" || fail "fresh install clones the pinned tag: $(cat "$LOG")"
grep -qx 'make -C voicegateway up image=ghcr.io/mahimailabs/voicegateway:1.0.0-preview.3' "$LOG" || fail "make up uses the pinned image"
grep -qx 'make -C voicegateway preflight image=' "$LOG" || fail "preflight runs after up"
grep -q 'make user-add' "$WORK/out" || fail "next steps name make user-add"
pass "fresh install clones the release, starts it from the image and runs preflight"

run_install || fail "re-run exited non-zero"
grep -qx 'git -C voicegateway fetch --depth 1 origin tag v1.0.0-preview.3' "$LOG" || fail "re-run fetches the tag"
grep -qx 'git -C voicegateway checkout --quiet v1.0.0-preview.3' "$LOG" || fail "re-run checks out the tag"
grep -q ' clone ' "$LOG" && fail "re-run must not clone again"
pass "re-running updates the existing checkout"

run_install VOICEGATEWAY_VERSION=1.2.3 VOICEGATEWAY_DIR=elsewhere || fail "override exited non-zero"
grep -q -- '--branch v1.2.3 .* elsewhere$' "$LOG" || fail "VOICEGATEWAY_VERSION and VOICEGATEWAY_DIR are honoured"
grep -q 'image=ghcr.io/mahimailabs/voicegateway:1.2.3' "$LOG" || fail "the image follows VOICEGATEWAY_VERSION"
pass "VOICEGATEWAY_VERSION and VOICEGATEWAY_DIR override the defaults"

mkdir -p "$WORK/notrepo"
run_install VOICEGATEWAY_DIR=notrepo && fail "a non-checkout directory must be refused"
grep -q 'not a VoiceGateway checkout' "$WORK/out" || fail "refusal names the reason"
pass "an existing directory that is not a checkout is refused"

run_install 'VOICEGATEWAY_VERSION=1.0 latest' && fail "a malformed version must be refused"
grep -q 'must look like' "$WORK/out" || fail "version refusal explains the format"
pass "a malformed version is refused before anything runs"

run_install DOCKER_COMPOSE_FAILS=1 && fail "missing Compose v2 must be refused"
grep -q 'Compose v2 is missing' "$WORK/out" || fail "Compose refusal names Compose"
run_install DOCKER_INFO_FAILS=1 && fail "a stopped Docker must be refused"
grep -q 'not running' "$WORK/out" || fail "Docker refusal says it is not running"
grep -q '^git' "$LOG" && fail "nothing is downloaded when Docker is unusable"
pass "missing Compose or a stopped Docker is refused before anything is downloaded"

mv "$STUBS/make" "$WORK/make.off"
run_install && fail "missing make must be refused"
grep -q 'Install make first' "$WORK/out" || fail "refusal names make"
mv "$WORK/make.off" "$STUBS/make"
pass "a missing tool is named"

env -i PATH="$SYS" bash "$REPO_ROOT/collector.sh" > "$WORK/out" 2>&1 && fail "collector.sh must exit non-zero"
grep -q 'voicegateway.dev/install.sh' "$WORK/out" || fail "collector.sh points at install.sh"
pass "collector.sh installs nothing and points at install.sh"

run_install TEST_ORIGIN=https://github.com/example/unrelated.git && fail "an unrelated repository must be refused"
grep -q 'official repository' "$WORK/out" || fail "origin refusal explains the problem"
grep -Eq '^make| fetch | checkout ' "$LOG" && fail "unrelated checkout must not be changed or run"
pass "unrelated origin refused before fetch, checkout or make"
run_install 'TEST_DIRTY= M Makefile' && fail "local edits must be refused"
grep -q 'local changes' "$WORK/out" || fail "dirty refusal explains the problem"
grep -Eq '^make| fetch | checkout ' "$LOG" && fail "dirty checkout must not be changed or run"
pass "local edits refused before fetch, checkout or make"
