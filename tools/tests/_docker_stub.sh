#!/usr/bin/env bash
# Fake docker for collector.sh tests. Records calls; "compose version" and
# "compose up" succeed; everything else exits 0.
echo "[docker-stub] $*" >> "${DOCKER_STUB_LOG:-/dev/null}"
case "$1 ${2:-}" in
    "compose version") echo "Docker Compose version v2.0.0";;
    *) : ;;
esac
exit 0
