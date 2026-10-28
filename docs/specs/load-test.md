# Load test: 1,000 concurrent calls, 0 events lost

Roadmap #320, last item of "SDK, docs and release". It proves two launch targets from PRODUCT.md and ARCHITECTURE.md: no event is lost at 1,000 concurrent calls, and web answers an ingest request in under 50 ms at p99. No LiveKit server is needed.

## What it sends

`tools/loadtest` replays one captured call many times over, each copy with its own ids:

- The call: the LiveKit Agents 1.8.7 call through livekit-server that `internal/api` tests store (`internal/api/testdata/full-call.*`): 10 webhooks, 56 spans and the session report, plus that agent's two metric exports (`internal/join/testdata/agents-1.8.7-metrics-1.binpb`, `-2`) and the SDK's example batch (`sdk/contract/examples/valid/full-call.json`).
- Ids: every LiveKit id (`RM_`, `EV_`, `AJ_`, `PA_`, `TR_`, `AD_`, `AW_`, `A_`), the room name and the trace id carry the run tag and the call number, so a run's rows are found by prefix and no two calls share an id.
- Time: every instant in every payload (RFC 3339, and Unix seconds, milliseconds, microseconds or nanoseconds) moves onto the replayed call. `-stretch` multiplies the gaps, to hold calls open longer.
- Webhooks go through livekit/protocol's own `URLNotifier`, the sender livekit-server uses: its 10 workers, its queue of 100 per worker that drops when full, its signing and its retries.
- Agent uploads go to LiveKit's paths with a bearer token holding the observability write grant: span batches every 5 seconds (the batch span processor's default), the metric exports, then the session report (header and chat history, no audio) when the job ends. The SDK batch goes to `/sdk/v1/events`. A 5xx or a refused connection is retried, as the exporters do.

## Acceptance checks

After the last send, the tool waits for the worker's consumer group to have no unread and no unacknowledged entries, then for every call's summary, and reads the stack's own databases:

1. No delivery refused, given up, or dropped by LiveKit's notifier.
2. Every webhook event (by its event id), trace export, span (by trace id), metric export, session report (by job id) and SDK batch (by batch id) is stored in ClickHouse, counted with `FINAL`.
3. Postgres holds every call, each ended as observed, with its agent job and both participants.
4. Every webhook, span and session report is joined to a call; no event is unmatched.
5. Every call has a summary, and every summary counts the same turns (they are the same call).
6. No new entry on the dead-letter stream.
7. Each upload kind is answered in under 50 ms at p99.

## Run it

Start the stack, add a project kept for load tests, and give the tool the same database URLs the binary uses (the tool reads ClickHouse, Postgres and Redis to count what was stored):

```sh
LIVEKIT_API_SECRET=<a random secret> voicegateway project add --name "Load test" --id load \
  --livekit-url wss://load.invalid --livekit-api-key APIloadtest --skip-verify
LIVEKIT_API_KEY=APIloadtest LIVEKIT_API_SECRET=<the same secret> \
VOICEGATEWAY_CLICKHOUSE_URL=... VOICEGATEWAY_POSTGRES_URL=... VOICEGATEWAY_REDIS_URL=... \
  make load-test LOADTEST_FLAGS="-calls 1000 -ramp 20s -out load.json"
```

Every run leaves its calls in that project, so keep it off a production instance. The tool exits non-zero when any check fails. `-ramp 60s -stretch 4` holds all 1,000 calls open together for about two minutes. Run the generator on other cores than the stack (for example `taskset`), or it competes with what it measures.

## Results, 2026-10-28

Main at b4cc22c, one Linux machine (4 cores, 15 GB), the six-service Compose stack with web and worker run as binaries. Every run used fresh ids.

| Run | Stack cores | Calls live at once | Lost | Slowest p99 (kind) | Peak backlog | Stored after last send | Summaries after last send |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Burst: 1,000 calls start in 20 s | 4 (shared with the generator) | 1,000 for about 22 s | 0 | 24.8 ms (session report) | 6,449 entries | 13.1 s | 2 min 21 s |
| Sustained: 60 s ramp, stretch 4 | 4 (shared with the generator) | 1,000 for about 2 min | 0 | 18.0 ms (session report) | 259 entries | 0.5 s | 2 min 52 s |
| Burst | 2, generator on the other 2 | 1,000 for about 22 s | 0 | 375 ms (session report) | 10,053 entries | 30.8 s | 6 min 56 s |
| Sustained | 2, generator on the other 2 | 1,000 for about 2 min | 0 | 224 ms (traces) | 807 entries | 0.5 s | 5 min 43 s |

A last burst with the finished tool, through `make load-test` on 4 cores, passed all 23 checks: 0 lost, slowest p99 29.0 ms (metrics).

Each run delivered 10,000 webhooks, 56,000 spans in 8,000 to 12,000 trace exports, 2,000 metric exports, 1,000 session reports and 1,000 SDK batches, and every one was stored, joined to its call and summarised. Peak use on 4 cores during the burst: web 61% of a core and 68 MB, worker 30% and 127 MB, ClickHouse 212% and 1.5 GB. On 2 cores held open: web 27% and 67 MB, worker 17% and 82 MB, ClickHouse 176% and 1.8 GB.

Findings:

- **Both targets hold on 4 cores.** 0 events lost, and every upload kind answered under 25 ms at p99.
- **2 cores keep every event but miss the p99 target.** In the burst p99 rose to 154 ms for webhooks and 375 ms for uploads, and webhooks waited up to 1.25 s in LiveKit's notifier queue; held open, p99 still reached 182 ms for webhooks and 224 ms for traces. ClickHouse took 176% of the 2 cores, most of it rebuilding summaries, which leaves web little room. That queue drops a webhook once 100 wait on one of its 10 workers, so a slower stack, or a larger burst, loses webhooks at LiveKit before VoiceGateway sees them. Cloud runs on 2 vCPUs.
- **Call summaries trail the events by minutes.** The worker reads each call's spans, metric points and events back from ClickHouse to build its summary, about 100 ms of ClickHouse time per call, while ClickHouse runs at 150 to 200% of a core. The call list shows a call without its figures until then.
- **ClickHouse 26.1.12 answers a prefix match on a FixedString key wrongly.** `startsWith(trace_id, …)` and `LIKE '…%'` on `spans` returned 7,632 of 56,000 stored rows, and the full count with `use_primary_key = 0`. VoiceGateway matches trace ids only by equality and `IN`, which return every row; the tool matches by `substring`.
- **A generator that bursts measures itself.** An early version built all 1,000 plans (17 ms each) after setting a 3 second head start, so 14 seconds of webhooks fired at one instant and LiveKit's notifier dropped up to 2,110. Plans are now built on every core with a measured head start, and the tool stops if a plan is still being built when its call should start.
