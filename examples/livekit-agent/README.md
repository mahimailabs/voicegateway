# Example LiveKit agent

The smallest LiveKit Agents voice agent, connected to VoiceGateway by one variable: `LIVEKIT_OBSERVABILITY_URL`. It imports nothing from VoiceGateway.

- `agent.py`: speech to text, model and text to speech through LiveKit Inference. The session records (`record=True`), which is what makes LiveKit Agents upload it.
- `call.sh`: one call with no human. It starts the agent, dispatches it to a new room with `lk dispatch create`, joins as a caller with `lk room join --publish caller.ogg`, leaves, and stops the agent after its session upload.
- `caller.ogg`: 2 s of silence, one spoken question, 20 s of silence. Ogg Opus, 48 kHz mono.

## Run

Needs [uv](https://docs.astral.sh/uv/) and the [LiveKit CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/) (`lk`).

```bash
cp examples/livekit-agent/.env.example examples/livekit-agent/.env
# fill in the four values
make example-call
```

LiveKit Inference runs on LiveKit Cloud, so the LiveKit values must be a LiveKit Cloud project. VoiceGateway itself can be self-hosted or Cloud. On a self-hosted LiveKit server, replace the three model strings with provider plugins.

## Versions

Tested with livekit-agents 1.8.8 (pinned in `uv.lock`) and lk 2.19.0. The upload needs livekit-agents 1.8.6 or later.

## Caller audio

`caller.ogg` was made on macOS with:

```bash
say -v Samantha -o caller.aiff "Hi, what time do you open tomorrow?"
ffmpeg -i caller.aiff -af "adelay=2000,apad=pad_dur=20" -ac 1 -ar 48000 \
  -c:a libopus -b:a 24k -frame_duration 20 -map_metadata -1 -fflags +bitexact caller.ogg
```

`lk room join --publish` takes `.ogg` (Opus), `.h264` and `.ivf`, not WAV.

## Checks

```bash
cd examples/livekit-agent
uv lock --check
uv run ruff check && uv run ruff format --check && uv run mypy
```
