#!/usr/bin/env bash
# One scripted call: start the agent, dispatch it to a new room, join as a
# caller that plays caller.ogg, leave, then stop the agent once its session
# upload is done. Reads LiveKit and VoiceGateway settings from ./.env.
#
# ATTACH=1 runs agent_attach.py instead: the Python SDK, voicegateway.attach(),
# with LiveKit's uploader off, so the call reaches VoiceGateway through the SDK only.
# ATTACH=upload runs agent_attach.py with the uploader on as well.
set -euo pipefail

cd "$(dirname "$0")"

AGENT_NAME=voicegateway-example
ROOM="example-call-$(date +%s)"

if [ ! -f .env ]; then
	echo "examples/livekit-agent/.env is missing: copy .env.example to .env and fill it in" >&2
	exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a
if [ -n "${ATTACH:-}" ]; then
	agent_script=agent_attach.py
	connection=VOICEGATEWAY_URL
	if [ "$ATTACH" = upload ]; then
		connection="VOICEGATEWAY_URL LIVEKIT_OBSERVABILITY_URL"
	else
		# SDK only: LiveKit Agents treats an empty LIVEKIT_OBSERVABILITY_URL as unset, and
		# load_dotenv in the agent does not override a variable that exists.
		export LIVEKIT_OBSERVABILITY_URL=
	fi
else
	agent_script=agent.py
	connection=LIVEKIT_OBSERVABILITY_URL
fi
# shellcheck disable=SC2086 # connection names one or two variables
for name in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET $connection; do
	if [ -z "${!name:-}" ]; then
		echo "$name is empty in examples/livekit-agent/.env" >&2
		exit 1
	fi
done
command -v lk >/dev/null || {
	echo "lk (the LiveKit CLI) is not installed: https://docs.livekit.io/reference/developer-tools/livekit-cli/" >&2
	exit 1
}

uv sync --locked --quiet

log="$(mktemp "${TMPDIR:-/tmp}/voicegateway-example-agent.XXXXXX")"
agent_pid=""
cleanup() {
	if [ -n "$agent_pid" ] && kill -0 "$agent_pid" 2>/dev/null; then
		kill -TERM "$agent_pid" 2>/dev/null || true
		wait "$agent_pid" 2>/dev/null || true
	fi
	rm -f "$log"
}
trap cleanup EXIT

# start mode, not dev: only start drains running jobs on SIGTERM, and the
# session report is uploaded while the job shuts down.
.venv/bin/python "$agent_script" start --drain-timeout 60 > >(tee "$log") 2>&1 &
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

lk dispatch create --room "$ROOM" --agent-name "$AGENT_NAME"

# Join only once the agent has the job, so it hears the whole question.
for _ in $(seq 30); do
	grep -q "received job request" "$log" && break
	sleep 1
done
grep -q "received job request" "$log" || {
	echo "the agent did not receive the job within 30 s" >&2
	exit 1
}

# caller.ogg is 2 s of silence, one question and 20 s of silence: time for the
# agent to answer before the caller leaves and the call ends.
lk room join --identity caller --publish caller.ogg --exit-after-publish "$ROOM"

echo "caller left room $ROOM; stopping the agent after its session upload"
kill -TERM "$agent_pid"
status=0
wait "$agent_pid" || status=$?
agent_pid=""
# 143 is the agent stopping on our SIGTERM; anything else is a failed shutdown.
if [ "$status" -ne 0 ] && [ "$status" -ne 143 ]; then
	echo "the agent exited with status $status while shutting down" >&2
	exit "$status"
fi
echo "done: open the console to see the call in room $ROOM"
