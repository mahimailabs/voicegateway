# Demo calls

A LiveKit Agents voice agent for a small bakery, with several call shapes, for filling a VoiceGateway project with varied, real calls. The [example agent](../livekit-agent/README.md) is the smallest connection; this one is for a demo.

| Scenario | What the caller does | Models (LiveKit Inference) |
| --- | --- | --- |
| `quick` | Asks when the bakery opens | Deepgram Nova-3, GPT-4.1 mini, Cartesia Sonic 3 |
| `slow-llm` | Asks a price that needs working out | AssemblyAI Universal-3.6 Pro, GPT-5, Deepgram Aura-2 |
| `interruption` | Asks about every cake, then cuts in 6 s later | Deepgram Nova-3, GPT-4.1 mini, Fish Audio S2.1 Pro |
| `tool-call` | Asks about order 1234; the agent calls `check_order` | Deepgram Flux, Gemma 4 31B, Rime Coda |
| `phone` | A real phone call through a LiveKit number | Same as `tool-call` |

Each scenario is a different stack on purpose, so the models layer has several providers to compare. Nothing is slowed down by hand: the slow call is a reasoning model on a question that needs it.

The agent uses both connections: LiveKit's uploader (`LIVEKIT_OBSERVABILITY_URL`, `record=True`) sends spans, turns and the recording, and `voicegateway.attach()` from the [Python SDK](../../sdk/python/README.md) puts `scenario` and `demo` on each call as custom fields, so the call list filters by scenario.

## Files

- `agent.py`: the agent. It reads the scenario from the dispatch metadata, `{"scenario": "slow-llm"}`; no metadata means `phone`.
- `make-callers.sh`: makes `callers/<scenario>.ogg` with macOS `say` and ffmpeg.
- `phone-dispatch.json`: a dispatch rule that sends each call to the number into its own room with this agent.
- `run-demo.sh`: starts the agent once, then places `ROUNDS` calls (default 3) per scenario, each in a new room, with `lk dispatch create --metadata` and `lk room join --publish callers/<scenario>.ogg`.

## Run

Needs [uv](https://docs.astral.sh/uv/), the [LiveKit CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/) (`lk`), and a LiveKit Cloud project (LiveKit Inference runs there).

```bash
cd examples/demo-calls
cp .env.example .env    # fill in the five values
./make-callers.sh       # once, on a Mac
./run-demo.sh           # ROUNDS=5 ./run-demo.sh for more calls
```

To see the SFU and SIP layers too, point the LiveKit project's webhook at `https://<host>/livekit/webhook`.

### A phone call

LiveKit Phone Numbers take inbound calls only, in the US; a Build plan project includes one number.

```bash
lk sip dispatch create phone-dispatch.json          # prints the rule id
lk number search --country-code US
lk number purchase --numbers +1XXXXXXXXXX --sip-dispatch-rule-id <RULE_ID>
.venv/bin/python agent.py start
```

Then call the number and ask about order 1234.

## Cost

On LiveKit Inference prices, a demo call costs cents: the models above are billed per minute of speech, per character spoken and per token. A Build plan project includes $2.50 of Inference credits; `ROUNDS=3` places 12 calls of about 30 s each.

## Checks

```bash
uv lock --check
uv run ruff check && uv run ruff format --check && uv run mypy
```
