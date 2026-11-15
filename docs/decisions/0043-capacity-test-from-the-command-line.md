# 0043: The capacity test runs from one command, refines its number and writes a report

- Status: accepted
- Date: 2026-11-15
- Amends: 0022

## Context

Decision 0022 starts a capacity test only from a click on an agent in the console. The proof of concept on 2026-11-15 (one AWS agent server, run 319eabb4) showed what that costs a stranger: six services, a user, a project, one call so the agent is listed, then the click. The number itself came from the worker's runner alone. It also showed three gaps in what the test says: the step that stopped at 32 calls read "job_failed" when the agent's worker had refused 8 calls and dropped 1; doubling steps proved 16 when the agent carried about 24; and the median hid that 23% of answers took over a second. Mahimai agreed the one-command path and a report carrying "Measured with VoiceGateway" with a link (2026-11-15).

## Decision

- `voicegateway capacity --livekit-url --agent --max` runs the same runner, ramp, stop rules, idle check and cleanup as the worker, on an in-memory run with no database, console or sign-in. A person starts it, so 0022's rule holds in spirit: never on a schedule, and never without consent. The command explains that the calls are real and billed by the agent's providers and asks, unless `--yes`; with no terminal to ask on, it places no calls.
- It ships in the open-source image and binary (MIT), like the console's test.
- After the first step that fails on the agent's own limit, the command tests midpoints between the last pass and the first fail, from no calls up, until the gap is at most an eighth of the failed step (16 passed, 32 failed: 24, then 20 or 28). `--no-refine` turns it off. The console's run does not refine yet; its chart assumes the doubling ladder.
- A call the agent leaves no longer ends its step at once. The step goes on until every call it dispatched has had the 10 s pending rule, so a worker that is full reads as "refused new calls (worker full)" with how many calls never got the agent, and a drop with nothing pending stays `job_failed`. A drop still fails the step.
- A report states measured facts only: the number, one sentence on what stopped it, calls and answers per step with median, p95, share over one second and slowest, what the test does not measure, and the run's id, version and times. It is one self-contained HTML page (no script, font or image from the network), the same as Markdown, and the raw JSON. It carries "Measured with VoiceGateway" and a link to voicegateway.dev. Fix advice stays out of it.
- The LiveKit key and secret come from `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET`, never flags, and appear in no output; the report names only the LiveKit host.

## Consequences

- Anyone with Docker and a LiveKit key can measure an agent in one command; the paid capacity test offer uses the same command.
- A refined run takes longer: each refine step waits 20 s for the agent to finish its jobs, then holds its calls for 60 s.
- A step with a dropped call now lasts up to 10 s longer than before, and its record counts the calls that never got the agent.
- Not covered, each its own later spec: CPU and memory on the agent's servers, per-stage latency, phone callers, the console's refine.
