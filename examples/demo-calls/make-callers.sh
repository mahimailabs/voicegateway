#!/usr/bin/env bash
# Makes the caller audio for run-demo.sh with macOS `say` and ffmpeg: one Ogg Opus file per
# scenario, 48 kHz mono, which is what `lk room join --publish` takes. Run it on a Mac.
set -euo pipefail

cd "$(dirname "$0")"
command -v say >/dev/null || { echo "say is macOS only" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is not installed" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

speak() { say -v Samantha -o "$tmp/$1.aiff" "$2"; }

# opus <out> <ffmpeg filter>: encode the filtered audio the same way as examples/livekit-agent.
opus() {
	ffmpeg -loglevel error -y "${@:3}" -filter_complex "$2" -ac 1 -ar 48000 \
		-c:a libopus -b:a 24k -frame_duration 20 -map_metadata -1 -fflags +bitexact "callers/$1.ogg"
}

mkdir -p callers

# One question, then silence for the answer.
speak quick "Hi, what time do you open tomorrow?"
opus quick "[0]adelay=2000,apad=pad_dur=15" -i "$tmp/quick.aiff"

# A question with arithmetic in it, and more silence: the reasoning model takes longer.
speak slow "I need a cake for forty people, with no nuts and no dairy. How much would that cost?"
opus slow-llm "[0]adelay=2000,apad=pad_dur=25" -i "$tmp/slow.aiff"

# A question with a long answer, then the caller cuts in 6 s after asking.
speak ask "Can you tell me about all of your cakes?"
speak cut "Sorry, sorry. Just the chocolate one. How much is it?"
opus interruption "[0]adelay=2000[a];[1]adelay=$((2000 + 6000 + $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$tmp/ask.aiff" | awk '{printf "%d", $1 * 1000}')))[b];[a][b]amix=inputs=2:duration=longest:normalize=0,apad=pad_dur=15" \
	-i "$tmp/ask.aiff" -i "$tmp/cut.aiff"

# An order lookup: the agent calls check_order.
speak order "Hi, can you check on my order? The number is one two three four."
opus tool-call "[0]adelay=2000,apad=pad_dur=18" -i "$tmp/order.aiff"

ls -l callers
