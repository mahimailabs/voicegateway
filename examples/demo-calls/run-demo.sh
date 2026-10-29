#!/usr/bin/env bash
# Runs the demo calls: starts the agent once, then for each round places one call per
# scenario, each in a new room with the scenario in the dispatch metadata and a caller that
# plays callers/<scenario>.ogg. Reads LiveKit and VoiceGateway settings from ./.env.
#
# ROUNDS (default 3) sets how many calls each scenario gets.
# SCENARIOS (default "quick slow-llm interruption tool-call") picks the scenarios.
set -euo pipefail

cd "$(dirname "$0")"

AGENT_NAME=voicegateway-demo
ROUNDS="${ROUNDS:-3}"
SCENARIOS="${SCENARIOS:-quick slow-llm interruption tool-call}"

if [ ! -f .env ]; then
	echo "examples/demo-calls/.env is missing: copy .env.example to .env and fill it in" >&2
	exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a
for name in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET LIVEKIT_OBSERVABILITY_URL VOICEGATEWAY_URL; do
	if [ -z "${!name:-}" ]; then
		echo "$name is empty in examples/demo-calls/.env" >&2
		exit 1
	fi
done
for scenario in $SCENARIOS; do
	[ -f "callers/$scenario.ogg" ] || {
		echo "callers/$scenario.ogg is missing: run ./make-callers.sh on a Mac" >&2
		exit 1
	}
done
command -v lk >/dev/null || {
	echo "lk (the LiveKit CLI) is not installed: https://docs.livekit.io/reference/developer-tools/livekit-cli/" >&2
	exit 1
}

uv sync --locked --quiet

log="$(mktemp "${TMPDIR:-/tmp}/voicegateway-demo-agent.XXXXXX")"
agent_pid=""
cleanup() {
	if [ -n "$agent_pid" ] && kill -0 "$agent_pid" 2>/dev/null; then
		kill -TERM "$agent_pid" 2>/dev/null || true
		wait "$agent_pid" 2>/dev/null || true
	fi
	rm -f "$log"
}
trap cleanup EXIT

# start mode, not dev: each job uploads its session report while it shuts down, and only
# start drains running jobs on SIGTERM.
.venv/bin/python agent.py start --drain-timeout 60 > >(tee "$log") 2>&1 &
agent_pid=$!

echo "waiting for the agent to register with LiveKit"
for _ in $(seq 60); do
	grep -q "registered worker" "$log" && break
	kill -0 "$agent_pid" 2>/dev/null || {
		echo "the agent exited before registering" >&2
		exit 1
	}
	sleep 1
done
grep -q "registered worker" "$log" || {
	echo "the agent did not register within 60 s" >&2
	exit 1
}

calls=0
for round in $(seq "$ROUNDS"); do
	for scenario in $SCENARIOS; do
		room="demo-$scenario-$(date +%s)"
		jobs_before="$(grep -c "received job request" "$log" || true)"
		echo "round $round: $scenario in room $room"
		lk dispatch create --room "$room" --agent-name "$AGENT_NAME" \
			--metadata "{\"scenario\": \"$scenario\"}"

		# Join only once the agent has the job, so it hears the whole caller audio.
		for _ in $(seq 30); do
			[ "$(grep -c "received job request" "$log" || true)" -gt "$jobs_before" ] && break
			sleep 1
		done
		[ "$(grep -c "received job request" "$log" || true)" -gt "$jobs_before" ] || {
			echo "the agent did not receive the job for room $room within 30 s" >&2
			exit 1
		}

		# --auto-subscribe: the agent speaks only once someone subscribes to its audio track.
		lk room join --identity caller --auto-subscribe --publish "callers/$scenario.ogg" \
			--exit-after-publish "$room"
		calls=$((calls + 1))
		# The caller left, so the room closes and the job uploads its session; give it a
		# moment before the next call.
		sleep 5
	done
done

echo "placed $calls calls; stopping the agent after the last session upload"
kill -TERM "$agent_pid"
status=0
wait "$agent_pid" || status=$?
agent_pid=""
# 143 is the agent stopping on our SIGTERM; anything else is a failed shutdown.
if [ "$status" -ne 0 ] && [ "$status" -ne 143 ]; then
	echo "the agent exited with status $status while shutting down" >&2
	exit "$status"
fi
echo "done: open the console to see the calls, filtered by the scenario field"
