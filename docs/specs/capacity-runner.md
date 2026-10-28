# Capacity test runner

## Goal

Run the capacity test of decision 0022 for real. From one click on an agent, the worker ramps synthetic calls at it until a stop rule trips, and the console shows the result: calls at once, and what limited it. Roadmap #320: "Capacity test runner with its stop rules, decision 0022".

## Context

- Decision 0022 (the test, its stop rules, `vg-capacity-*` rooms), PRODUCT.md capability 8, principle 4 (never in the call path).
- The 2026-10-14 spike: CreateRoom, CreateDispatch by agent name, a Go caller that waits for the greeting, says one line and times the answer from the agent's `lk.agent.state` attribute. Its run is the captured `dashboard/src/mocks/capacity.json`.
- `docs/specs/capacity-telemetry-audit.md`: keep runner telemetry apart from target telemetry; a runner limit never reads as an agent limit; do not claim exact maxima from sampled gauges.
- The console's capacity dialog and result (`dashboard/src/features/agents/capacity.tsx`), today with Start disabled.

## Libraries

- server-sdk-go v2.19.0 for everything LiveKit: `RoomServiceClient.CreateRoom` and `DeleteRoom`, `AgentDispatchServiceClient.CreateDispatch` and `ListDispatch`, `lksdk.ConnectToRoom` for the caller, `lksdk.NewLocalFileTrack` to play the caller's Ogg Opus line, `ParticipantAgent` and `RemoteParticipant.Attributes` for the agent's join and state.
- Considered and not used: livekit-cli v2.19.2 `pkg/loadtester.AgentLoadTester` (`lk perf agent-load-test`). It opens a fixed number of rooms with an echo caller and prints a table; its stats are unexported, it waits forever for a refused job, it has no answer time and no ramp, and it pins an unreleased livekit/protocol pseudo-version. The runner follows its approach (named dispatch, agent join by participant kind) through the same SDK.
- gopsutil v4 (already in the module graph) for the tester's CPU.
- The caller's line is `examples/livekit-agent/caller.ogg`'s question, cut to the speech with ffmpeg; no speech is synthesised.

## In Scope

1. **Start** (`startCapacityRun`, Admin): agent name, most calls at once (8, 16, 32 or 64), and consent to place real calls through the agent's providers. One run per project at a time (409 `run_active`). An unnamed agent cannot be tested: explicit dispatch needs a name (400).
2. **Runner in the worker** (`internal/capacity`): claims a requested run from Postgres (`FOR UPDATE SKIP LOCKED`), heartbeats it every second, and runs it. A run whose heartbeat is 30 s old is ended as `runner_lost` by any worker, so a crashed worker never leaves a run open.
3. **Idle check**: before step 1 the runner lists the project's rooms and their dispatches; if any room it did not create has a job for the agent, the run ends `agent_busy` with no room created. The same check every 10 s during the run ends it as `real_call`.
4. **Ramp**: steps 1, 2, 4 up to the maximum, 60 s each. Calls stay up across steps: step 2n adds n calls to the n already there. Each new call is a room `vg-capacity-<run>-<n>` (60 s empty timeout, metadata naming it a test), a named dispatch, and a caller that subscribes, waits for the agent to finish its greeting, then says the line, times the answer, and says it again after each answer until the step ends. The caller's participant carries the attribute `voicegateway.capacity-test=true` so an agent can skip side effects.
5. **Stop rules** (decision 0022), checked during and at the end of each step:
   - `pending`: a new call's agent has not joined 10 s after its dispatch.
   - `job_failed`: an agent left a call before the step ended, or a dispatch was refused.
   - `answer`: the step's median answer is more than 50% above step 1's median, or a call got no answer within 15 s of its line.
   - `tester`: the tester's CPU averaged over 90% across the step. The step is then inconclusive, never an agent failure.
   - `real_call`, `stopped` (a person pressed Stop), `runner_lost`.
   - A step that passes every rule at the maximum ends the run as `max_reached`.
   - A join slower than the project's join threshold (Settings, `joinMs`) but within 10 s is reported per step as a late join, not a failure: it means the worker started a new process. Decision 0022 lists the join threshold among the stop rules and, two lines later, reports it separately; this spec takes the second reading, which the spike supports (8 calls joined at about 5.8 s while every call was answered).
6. **Result**: the last step every call passed (`capacity`, null when step 1 failed), `limitedBy`, and per step: calls, joined, pending, failed, late, join and answer seconds, tester CPU. A Cloud agent stopped by `pending` with exactly 5 calls joined is labelled `plan` (the Build plan's 5-session cap, decision 0022). Results are stored in Postgres (00025): a run is a few rows, written by the worker like the call index.
7. **Cleanup**: when a run ends for any reason, the runner disconnects every caller and deletes every room it created; the rooms' 60 s empty timeout covers a worker that dies first.
8. **Test rooms never count**: a `vg-capacity-` room's webhooks and uploads are joined like any call's, so nothing it sends is lost, but the `call_list` view marks it not servable (00025). Calls, call stats, Issues, alert findings and the first call read only servable calls, and the plan's call minutes (`OrganisationUsage`) leave test rooms out.
9. **Read and stop**: `getCapacity` returns the agent's latest run from Postgres, live while it runs; `stopCapacityRun` (Admin) asks the runner to stop, which it does within 2 s.
10. **Console**: Start is enabled for Admins; the dialog shows each step as it lands and a Stop button, then the result. Others see the result and a disabled Start that says who can run it.

## Out of Scope

- Native agent telemetry per step (worker load, provider latency per attempt): the audit's steps 1 to 5. The worker's 15 s worker-load samples stay on the Agent workers page.
- Callers on a machine other than the worker; network saturation of the tester (CPU only).
- Scheduled or repeated runs (0022: from a click only). Prices (0010).

## Constraints

- web never dials LiveKit for the run; it only writes the request. worker never answers HTTP.
- Never panic in the runner; a LiveKit error ends the run as `failed` with its error class, after cleanup.
- Unknown stays unknown: an answer never heard is not a zero; a step not reached is absent, not zero.
- Logs carry the run id, project and room names, never a token or secret.

## Acceptance Checks

1. Against livekit-server --dev and a test agent worker that accepts 4 jobs, a run to 16 passes 1, 2 and 4 and stops at 8 with `pending`, capacity 4 (TestRunStopsWhenAJobStaysPending).
2. A test agent whose answers slow down with load stops on `answer` at the first step more than 50% slower than step 1; exactly 50% passes (TestRamp, "answers more than 50% slower").
3. A room the runner did not create getting the agent ends the run as `real_call` within 10 s, and a busy agent at the start ends it as `agent_busy` before any room is created (TestRamp, "a real call mid-run"; TestBusyAgentIsNotTested; TestBusyAgentIsRefusedOnLiveKit against livekit-server --dev).
4. Stop ends a running test within 2 s; after any end, every `vg-capacity-` room is gone (TestRamp, "a person's stop", and every TestRamp case checks no test room is left; TestRunStopsWhenAJobStaysPending lists LiveKit's rooms; TestStopBeforeAWorkerClaimsIt).
5. A run whose worker stopped heartbeating is ended as `runner_lost`, and a second start for the project while one runs is 409 (TestLostRunnerEndsTheRun, TestLostRunIsNotEndedTwice, TestOneRunPerProject, TestCapacityRuns).
6. Tester CPU over 90% ends the step as `tester`, never as an agent limit; a slow join is late, not a failure (TestRamp, "a saturated tester", synctest with a fake CPU; TestSlowJoinIsLateNotAFailure).
7. A `vg-capacity-` room is never a servable call, never the first call, and never counts toward the plan's minutes (TestCapacityRoomsAreNotCalls).
8. Only an Admin starts or stops a run; routes, roles and the contract agree (TestCapacityRuns, TestEveryRouteHasAMinimumRole, `make api-check`).
9. The console starts a run, shows steps as they land, stops it, and shows the result (tests/capacity.test.tsx, tests/mock-api.test.ts "capacity tests"); Playwright with axe on the dialog in light and dark at phone and desktop sizes (e2e/capacity.spec.ts).
10. One real run on the Mac LiveKit test slot: the example agent on LiveKit Cloud, stack built from this branch, result stored and shown, every test room deleted.
