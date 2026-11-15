# One-command capacity test and report

Agreed by Mahimai on 2026-11-15, after the capacity proof of concept (run 319eabb4). Decision 0043 records what it changes in decision 0022.

## Goal

A stranger with a LiveKit voice agent gets a capacity number and a report they understand in under 5 minutes, with one command and no VoiceGateway install.

```bash
docker run --rm -it -v "$PWD:/out" -w /out -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET \
  ghcr.io/mahimailabs/voicegateway:<version> \
  capacity --livekit-url wss://... --agent my-agent --max 32
# reads LIVEKIT_API_KEY and LIVEKIT_API_SECRET from the environment
```

Output: a live table in the terminal, then `capacity-report.html`, `capacity-report.json` and `capacity-report.md` in the folder.

## Why (what the POC showed)

- Today a stranger needs: install six services, add a user, add a project, get one call through so the agent is listed, then click. Our own run needed the API to get around the last step.
- The number we sell came from one worker package (`internal/capacity`). The database, console and sign-in were not needed to produce it.
- The report was built by hand from the JSON. The README still says capacity tests "are still being built".
- Reference for the shape: github.com/agricidaniel/jev-seo. One sentence promise, the output shown before the install, one command, a real example committed, cost and limits stated plainly.

## In scope

1. **`voicegateway capacity` command.** Runs the existing runner (same ramp, stop rules, caller, cleanup) with an in-memory run store. No Postgres, Redis, ClickHouse or web. Flags: `--livekit-url`, `--agent`, `--max` (8, 16, 32, 64), `--out`, `--yes` (consent to place real calls; without it the command explains the cost and asks).
2. **Refine pass.** After the first failed step, test the midpoints between the last pass and the first fail (16 passed, 32 failed: try 24, then 20 or 28). The report gives a number, not a range. Off with `--no-refine`.
3. **Honest stop reason.** When the agent worker refuses new jobs, say "the agent refused new calls (worker full)" with the count dispatched and joined, not "job failed".
4. **Tail in the result.** Per step: median, p95, share of answers over 1 s, slowest. Also in the API and the console result.
5. **The report file.** One self-contained HTML page (no network needed), light and dark:
   - the number first, with one sentence on what stopped it
   - the load curve (calls carried, median and p95 answer per step)
   - step table
   - "what limited it" in plain words, from the stop reason
   - what the test does not measure
   - run id, versions, timings
   The same content as Markdown, and the raw JSON.
6. **Console.** "Download report" on the capacity result, same file. "Test capacity" reachable for a registered agent that has had no call yet.
7. **README.** New opening: the promise in one line, the report screenshot under it, the one command, then a committed real example (`examples/capacity/receptionist-c7i-xlarge/` from run 319eabb4: report, JSON, terminal capture). Sections "What it costs you" (test calls go through your own providers) and "Limits". Remove "capacity tests are still being built".

## Out of scope

- Agent box CPU and memory in the report (needs an exporter on the customer's box; separate spec).
- Phone or SIP callers, per-stage latency, audio quality score, cost per minute.
- Capacity tests on VoiceGateway Cloud.
- Fix recommendations written by a model. The report states measured facts; the fix list stays a human deliverable of the paid test.

## Acceptance checks

1. On a clean machine with Docker and a LiveKit key, the one command produces the three files with no other setup. Timed by a person who has never used VoiceGateway: under 5 minutes including the image pull.
2. Against `livekit-server --dev` and a test agent that accepts 4 jobs, the command reports capacity 4 and "refused, worker full".
3. With an agent that carries 16 and fails at 32, the refine pass reports a number between 16 and 31 and names the steps it tried.
4. The HTML report opens offline, reads at phone width, and passes axe in light and dark.
5. Ctrl-C deletes every test room it created.
6. No key or secret appears in the terminal output or any report file.
7. Console: download gives the same HTML; a never-called agent can be tested.

## Decisions

- One-command path: agreed ("Looks good, lets have one command path"). It ships in the open-source image unless Mahimai says otherwise; he was asked open-source or paid and did not pick the paid option.
- The report carries a "Measured with VoiceGateway" line with a link to voicegateway.dev: agreed.
