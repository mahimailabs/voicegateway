# Changelog

All notable changes to VoiceGateway are documented here. This project
follows [Semantic Versioning](https://semver.org/) and
[Conventional Commits](https://www.conventionalcommits.org/).

## Unreleased

### Added

- **A pinned baseline now records where its figures came from, and
  `voicegw baseline check` refuses when that does not match.** Exit code **3**,
  documented and stable.

  A local-pinned baseline checked against a collector produces numbers, a
  verdict and a green tick while being a comparison that never happened.
  Refused rather than warned, because a warning in CI output is read once and
  then never again: the whole value of a pinned baseline is that a comparison
  either happened or it did not, and a third state resembling the first is
  worse than an error, since an error stops the build.

  The project is part of that identity, not only the store. A collector
  aggregates many agents, so the same URL at a different project scope is a
  different population and a healthy-looking sample count can be somebody
  else's traffic. `check` output now names the source, window, project and
  minimum sample count it used.

  `BASELINE_VERSION` is 2, because the file shape changed. A v1 file is refused
  rather than compared against fields that moved.

  Groundwork for reading a remote collector, which is **not** in this release.

### Fixed

- **Tool-call rows are no longer dropped by a remote collector.**
  `/v1/ingest/tool-calls` did not exist, so every batch the agent posted was
  answered 405 and discarded.

  Reported by a consumer running against a collector. The sink is best-effort
  and requeues quietly, so nothing surfaced anywhere: a collector deployment
  simply had no tool rows and no error explaining why. Local-store deployments
  were unaffected.

  The route drops unknown keys and has no field for arguments or results, so a
  payload cannot enter the store through it even if an agent sent one. That
  keeps the guarantee on the collector side of the wire rather than trusting
  every agent that posts. An agent-supplied `id` is ignored, since honouring it
  would let one agent overwrite another's row.

### Added

- **A test that an older collector accepts a newer row shape.** Forward
  compatibility across a version-skewed fleet was already true, because every
  ingest parser drops unknown keys, but it held by accident rather than by
  contract.

  Agents and collectors upgrade at different times whether or not anyone intends
  it, and if that ever tightened the failure would be total (every cost and
  latency row rejected) rather than partial (one row type). `revision` is the
  concrete case: 0.25 agents send it and 0.24 collectors have never heard of it.

### Fixed

- **The `attach()` docstring no longer contradicts its own signature.** Shipped
  in 0.25.0 and reported by a consumer who read the API rather than the release
  notes.

  `revision` and `policy` were in the signature and absent from `Args`, so a
  reader checking the docstring concluded the release's two headline parameters
  did not exist. Worse, the four capture flags became `bool | None = None` while
  their entries still read "(default on)" and "(default OFF)", which were the
  pre-0.25.0 values: somebody passing nothing and trusting the docstring would
  believe transcript capture was on when the policy decides. A doc that is
  merely absent is a gap; one that states the opposite of the code is a trap.

  The three-layer precedence (environment kill-switch, then explicit flag, then
  policy) is now stated once, in `policy`. Two tests pin it: every public
  parameter is documented, and the docstring claims no default the signature
  dropped.

### Added

- **`attach(policy=...)`: one named capture policy instead of four booleans.**
  `standard`, `timing_only`, `lean`, `debug`, `off`. The four flags stay and
  behave identically, with no deprecation warning in this change.

  The set was the interesting thing and there was no way to name it. An operator
  who wanted "timing only, nothing the caller said" had to know that means three
  of the four off, get each right, repeat it at every call site, and know which
  environment variable overrides which. That is a policy expressed as four
  independent booleans, which is how a wrong combination ships without anybody
  deciding to ship it.

  The flags also mixed two unrelated questions. **What it costs to run**: dead
  air polls once a second for the life of every session, where turn capture
  costs nothing between events, and `lean` is `timing_only` without that
  standing cost. **What it discloses**: a transcript is what the caller said, a
  snapshot is the operator's own prompt and every tool payload, and `debug` is
  the only policy carrying snapshots.

  An explicit argument overrides the policy, so the four parameters are now
  tri-state: with plain boolean defaults there is no way to tell `transcript=True`
  passed deliberately from the default that happens to be True. An unknown
  policy name is refused rather than defaulted, because a typo that silently
  selected `standard` would turn "nothing the caller said" into transcript
  capture. Environment kill-switches still beat everything.

### Fixed

- **Tool-call rows now ride the `turns` flag.** They correlate to a turn and
  their `turn_index` is meaningless without one, so capturing them for somebody
  who asked not to capture turns wrote a row type they did not ask for and could
  not join to anything.

- **One row per tool call, from `tool_execution_updated`.** Tool name, start,
  duration and outcome (completed, failed, cancelled), correlated to the turn
  and session it belongs to, with per-tool aggregates on the read path.

  On agents that call tools the tool is usually the largest term in a slow turn,
  and it was the one term the views could not show. `llm_ttft_ms` and
  `tts_ttfb_ms` are both small and both visible; the multi-second external call
  sitting between them was neither.

  **No arguments and no results are captured anywhere.** There is no column for
  either, the capture reads the tool's name and never its `arguments`, and the
  collector wire shape is written field by field rather than dumping the model,
  so a payload column added later cannot start leaving the agent by accident.
  A name and a duration are a timing measurement; the payload is a disclosure,
  and keeping them apart is exactly what lets this default on. All three are
  asserted by tests rather than promised in comments.

  A call still in flight counts toward its tool's call count but not its
  average: treating a missing duration as zero would pull the average toward
  "instant" precisely when a tool hung, which is the case somebody went looking
  for.

- **`voicegw baseline pin` and `voicegw baseline check`: a drift gate for CI.**
  Pin a known-good window's figures to a file, recompute over a later window,
  exit non-zero when something moved.

  Latency and cost regressions are the ones that ship, because nothing fails
  when they do. A test suite catches an agent that is broken; nothing caught an
  agent that got 300ms slower or 40% more expensive per call, and the caller
  feels the first while the bill shows the second.

  **Exit codes are stable API**: `0` within tolerance, `1` drifted (named, with
  the delta), `2` could not compare. Tolerance is per metric rather than one
  global number, because cost and p95 do not move for the same reasons.

  What it refuses to call a pass: too few samples, a metric missing from the new
  window, a pinned zero, and an improvement beyond tolerance (a 50% drop is
  either very good news or a measurement that broke). Drift outranks
  insufficiency, so a real regression is never buried behind a warning about a
  different metric. A metric the new window *grew* is not a failure, otherwise
  every measurement added here would break every baseline on upgrade.

  Each metric carries the sample count it was taken over, and the file records
  what identifies the window, so a baseline is reproducible rather than a
  snapshot of an unnamed moment.

- **`attach(revision=...)` stamps which build of the agent's configuration
  produced every row.** Optional, with a `VOICEGW_AGENT_REVISION` fallback, on
  cost, latency, turn and dead-air rows.

  Rows carried `project`, `agent_id` and `tenant_id`, none of which says which
  version of the agent was running: the prompt, the model ids, the voice, the
  interruption thresholds. So "this got slower last Tuesday" was answerable only
  by joining deploy logs kept elsewhere against timestamps by hand, and that
  join stops working the moment two versions run at once, which is every canary
  and every gradual rollout.

  The value is **opaque**: a content hash, a git sha, a semver string and a
  deploy id are all valid, nothing parses it, it is only grouped and filtered
  by. The argument beats the environment, which is the opposite precedence to
  the capture kill-switches and deliberate: those override what an agent asked
  for, where this is the agent reporting a fact about itself.

  Absent stays absent. No hostname, no timestamp, no default, because an
  invented revision would silently split aggregates that belong together.

  Readable as `?revision=` on `/api/costs` (empty string selects rows that
  declared none) and as a `by_revision` rollup present on every cost summary, so
  a p95 spanning a configuration change shows as two agents rather than one
  blended figure.

### Fixed

- **The turns guide no longer documents a bug that was fixed, and now states
  which number answers "what did the caller wait".**

  It carried a note saying turn capture had not been given the
  `user_state_changed` fallback, so a caller's turn start could go uncaptured on
  `livekit-agents` 1.6. That stopped being true in `980cd8d`, and the page kept
  telling readers their turn data was unreliable.

  It also described `response_speed_ms` without saying that
  `caller_speak_end_ms` is corrected from `EOUMetrics.end_of_utterance_delay`,
  which is the only reason the figure means anything. Measured from the stop
  event instead, it would be short by the whole VAD silence window (0.55s on
  Silero's default) on every turn, always flatteringly, and the size of that
  error moves with each operator's `min_silence_duration`. The page now says so,
  and says why there is deliberately no second uncorrected column: absent reads
  as "not measured", where the uncorrected value would read as "fast".

  The turn-close description also now covers holding the turn open across tool
  calls.

- **A turn covering a tool call now reports the time to the answer, not to the
  holding line.** The turn is held open while any tool call is in flight, so the
  agent's filler audio no longer closes it.

  `response_speed_ms` is `agent_speak_start_ms - caller_speak_end_ms` and the
  turn closed on the agent's first audio frame. When an agent covers a slow tool
  call with "let me pull that up", that filler IS the first frame, so the row
  measured the wait to the filler. Filler during tool calls is a standard
  pattern, not an exotic one: `livekit-agents` ships `RunContext.with_filler`
  for exactly it.

  The error had the worst possible shape for an aggregate. Filler turns report
  fast numbers, so they pulled p50 DOWN rather than standing out as outliers,
  and the one turn somebody opened the latency view to investigate was the one
  that lied.

  Tools are tracked by `call_id` from `tool_execution_updated`, which was not
  previously bound at all. Done, error and cancelled all release the turn: a
  cancelled tool left in flight would hold the turn open and swallow the
  caller's next utterance, corrupting every later row in the call, which is
  worse than the mistimed row being fixed. A turn with no tool in flight never
  reaches the new branch and is byte-identical to before.

- **`voicegw` now runs on a bare `pip install voicegateway`.** It did not:
  `voicegw --help` raised `ModuleNotFoundError: No module named 'livekit'`
  before printing anything.

  `cli/__init__` imported every command module eagerly, and `livekit_cli`
  reaches `livekit_diag.admin`, which imports the LiveKit SDK at module scope.
  So a Pipecat or OpenRTC user could not reach `voicegw costs`, which needs no
  framework at all, without installing a framework they do not use. On a tool
  whose claim is that it is framework-agnostic, that is the claim failing at the
  first command.

  `livekit_cli` is the only command module affected: every other one was checked
  individually and imports clean, so exactly one import is guarded rather than
  all of them. With the SDK absent, `voicegw livekit` (and `voicegw livekit
  <anything>`) exits 2 naming the extra to install, rather than vanishing from
  the command list as though the feature did not exist.

- **Pricing no longer installs three weeks stale.** The `voice-prices>=0.1.0,<0.2`
  cap silently held new installs on the 0.1.0 dataset (2026-07-24) while 0.2.0
  and 0.3.0 shipped in August, so a freshly installed VoiceGateway costed calls
  against old prices. Now `>=0.3.0,<1`.

  The remaining `<1` is not caution about 0.x releases. It is the one guard
  against a deliberate breaking release landing in users' installs unannounced,
  which an unbounded runtime dependency cannot prevent.

### Added

- **The import warning's silence on an idle node is now pinned by a test.** No
  behaviour changes: this records an existing guarantee that nothing was
  asserting.

  A node that is scraped, correlates, carries no measured network throughput and
  has no `--network-baseline` entry produces no warning, because it produces no
  headroom row either and so has no UNKNOWN to warn about. Sourcing the warning
  from the scrape target list instead of from `bandwidth_peaks` reads as the more
  thorough option and would fire on every idle node in the fleet, and a warning
  that fires on non-problems is one operators learn to skip. Verified by
  mutation: widening the set to the correlated node list turns this test red and
  leaves the rest of the suite green.

### Fixed

- **The PyPI publish works again. `0.24.10` never reached PyPI**; install it from
  PyPI and you get `0.24.9`. The Docker images and the GitHub release for
  `0.24.10` published normally, so only the PyPI artifact is missing, and this
  release carries the same code plus this fix.

  Nothing was wrong with the wheel. `pyproject.toml` requires `hatchling` with no
  upper bound, so the build resolved hatchling 1.32.0, which writes
  `Metadata-Version: 2.5` unconditionally (a package with nothing but a name and
  a version gets it). The publish action was pinned to v1.14.0, whose bundled
  twine validates only up to 2.4, so `twine check` failed before upload with
  `InvalidDistribution: '2.5' is not a valid metadata version`. `0.24.9` shipped
  `Metadata-Version: 2.4` from an older hatchling, which dates the drift.

  Fixed by moving the pin to v1.14.2, which bundles twine v7 precisely to accept
  metadata 2.5. **Not** by pinning hatchling backwards: PyPI already accepts 2.5
  (hatchling's own wheel on PyPI carries it), so the stale checker was the whole
  defect and capping the builder would have been working around it.

- **A report no longer tells an operator to re-run a scrape that was already
  complete.** A gate that could not divide because nothing declared its
  denominator is now filed apart from a gate whose series was never scraped, and
  is sent to a re-import rather than a re-capture.

  The two read identically in the report and their remedies point opposite ways.
  On a real 500-concurrent hour, 32 `resource_headroom` gates went UNKNOWN
  because a SIP tier had scaled to 4 while the `--network-baseline` file still
  stopped at sip-2. `network_receive_bytes_total` was present in 257 of 257
  node_exporter samples for both nodes: nothing was missing from the scrape.
  `not_collected` said "the series was not in this run's scrape" and advised
  re-running against the current scrape configuration, an hour of fleet time
  that could not have changed the outcome. The gate's own detail line beside it
  said the true thing the whole time. Adding two lines of JSON and re-importing
  the same artifacts took 908 PASS / 51 UNKNOWN to 940 PASS / 19 UNKNOWN.

- **`voicegw loadtest import` now names the scraped nodes that have no declared
  network baseline.** Import is the only point where the declared names and the
  scraped names are both in hand, so it is the only place a tier that scaled
  since the baseline file was written can be caught.

  ```
  1 scraped node(s) carried network throughput with no declared baseline (sip-3),
  so their network headroom gates will read UNKNOWN.
  ```

  Warned on the nodes the bandwidth gate will actually iterate, so a node with
  no measured throughput produces no row and no warning. This is the fix that
  catches the class: the two above are the same omission found weeks later, in a
  report, one release before delivery.

- **`--network-baseline` and `--plan` ignore top-level keys starting with `_`,**
  so a declaration can carry its own provenance. Every top-level key was parsed
  as an entry, so a `"_comment"` naming where the published figures came from
  was refused as a malformed node. The documented copy in a repo therefore could
  not be loaded, a stripped duplicate got hand-placed on the collector, and the
  two drifted with nothing comparing them. That drift is what let the missing
  SIP tier above survive to the report.

- **The `return_to_baseline` gate no longer fails a node over a change its
  counter cannot meaningfully express.** A ratio outside tolerance is now a
  failure only when the absolute change also clears a per-metric floor.

  A pure ratio made the gate's sensitivity depend on the magnitude of the
  counter rather than on anything about the node. At 2,688 descriptors a 10%
  tolerance allows 268; at 864 it allows 86; at 320 it allows 32.

  `filefd_allocated` is quantized, which makes that concrete. Observed across a
  real fleet: 256 distinct values, every one a multiple of 32, smallest gap
  exactly 32. That is the kernel, not the exporter: `/proc/sys/fs/file-nr`'s
  allocated count is a percpu_counter read without folding in the per-CPU
  deltas, and the fold happens per `percpu_counter_batch`, which is
  `max(32, 2 x online CPUs)`. At 320 descriptors a single unavoidable tick of
  that counter is a 10% move and failed the gate on its own.

  The floor is 128 descriptors, four ticks on the common case, and 8 for
  `sockstat_udp_inuse`, which is not quantized but is a small integer count with
  the same magnitude problem. `memory_used_bytes` is **deliberately unfloored**,
  because it does not have the defect the floor exists to correct: its readings
  come from `/proc/meminfo` in kB, so the smallest change it can express is 1024
  bytes, roughly 0.0001% of a 1e9 reading. A floor there would buy nothing while
  being able to turn a measured failure into a pass.

  A suppressed failure says so. The detail reports the ratio that would have
  failed and the floor that stopped it, and the ratio stays on the result.

- **The `return_to_baseline` gate no longer lets one scrape decide a node's
  verdict.** Both sides of the ratio are now a median over their window, and the
  gate detail says how many samples each was taken over.

  The settle side used to be the newest row in `node_samples`. Generating the
  report straight after a run, which is the documented flow, therefore read the
  scrape that had just caught the collector host running the import and the
  report generator, and failed that node for the reporting tool's own footprint.
  The same run regenerated later passed, because by then the newest row was an
  ordinary one, so the verdict depended on when someone typed a command.

  The baseline side had the same fragility in the more dangerous direction: a
  spike there inflates the denominator, drags the ratio down and turns a real
  leak into a PASS. Both are now medians.

  Detection is not weakened. A leak holds the value up across the whole window,
  so the median moves with it; a transient cannot carry a verdict alone.

  **Gate details change wording**: `settled at 1344 (median of 7 samples)
  against a baseline of 1248 (median of 8 samples)`. A comparison built without
  sample counts still reads as before, and a window holding one reading says
  `(a single sample)` rather than calling itself a median.

- **The `return_to_baseline` gate takes its baseline from the run, not from
  whatever the table still holds.** Both windows are now anchored to the run
  window: the 5 minutes before it starts, and 5 to 10 minutes after it ends.

  The selection was "everything before the run" and "everything after it", each
  capped at 5,000 rows **ascending**. At a 15s cadence that cap spans about 20.8
  hours, so the rows it returned were the oldest retained history rather than
  anything near the run. On a real hour-long 500-concurrent run it produced a
  baseline from 21 hours earlier and reported a true 1.10x as **0.51x**: a
  marginal FAIL rendered as a confident PASS.

  It also coupled verdicts to retention. Which rows survive is
  `workers.node_sample_max_age_days`, so changing retention moved every baseline
  on every re-rendered report, with nothing connecting the two. Anchoring to the
  run window breaks that link, which is why this ships in the same release as
  that setting.

- **The settle window is measured from the end of the run.** It was measured
  from the baseline sample to the settled sample, which answers a different
  question and can be arbitrarily large while teardown is still draining, so
  `MIN_SETTLE_MS` could never fail.

  **Behaviour change**: a report generated before the settle window has elapsed
  now reports `UNKNOWN` rather than a verdict. Nothing has settled a minute
  after teardown, so there is no return to report either way. This is what
  `MIN_SETTLE_MS` always intended.

- **Gate details name the instant their numbers came from**, as
  `ending 2026-08-05T15:27:00Z`. Two reports carried a 21-hour-old baseline
  without anyone noticing, because no artifact said which samples produced the
  numbers and checking one meant querying the database.

### Changed

- **`voicegw_cost_usd_total` and `voicegw_requests_total` on `GET /v1/metrics`
  are now `# TYPE ... gauge`, not `counter`.** Both are sums over the `"today"`
  window, which is `time.time() - 86400`: a **rolling trailing 24 hours**, not a
  calendar day and not a since-start total. The value falls whenever a request
  ages out of the trailing edge, so it was never a counter. Declaring it one
  told Prometheus the series is monotonic, and `rate()` / `increase()` read
  every one of those decreases as a counter reset and extrapolate spend and
  traffic that never happened. The HELP text now states the window.

  **The metric names are unchanged**, deliberately. `docs/api/http-api.md` and
  `docs/reference/faq.md` publish them as things you graph in Grafana, and
  renaming a scraped series breaks every dashboard, alert and recording rule
  built on it. So the `_total` suffix survives on a gauge, which is ugly and
  against Prometheus naming convention. It is a wart we are choosing over a
  broken contract; a rename would be a separate, announced breaking change.

  **Migration for existing dashboards.** Nothing to change on the scrape side:
  same endpoint, same names, same labels. Prometheus accepts the new type on the
  next scrape with no config change and no re-ingest. What you must fix is any
  query that treats these as counters:

  | Before (silently wrong) | After |
  |---|---|
  | `rate(voicegw_cost_usd_total[5m])` | `voicegw_cost_usd_total{period="today"}` |
  | `increase(voicegw_cost_usd_total[1h])` | `delta(voicegw_cost_usd_total{period="today"}[1h])` |
  | `rate(voicegw_requests_total[5m])` | `sum(voicegw_requests_total)` |
  | `increase(voicegw_requests_total[24h])` | `sum(voicegw_requests_total)` (already the last 24h) |

  Historical samples already stored in Prometheus are unaffected: the values
  were always the rolling-window numbers, only the type metadata was wrong, so
  fixing the query fixes the whole retained history too. Two further traps worth
  checking while you are in there: bare `sum(voicegw_cost_usd_total)`
  triple-counts, because that series is emitted once with `period="today"`, once
  per `provider` and once per `project`; and there is no monotonic spend counter
  to migrate to, so if you need a since-start total use
  `GET /v1/costs?period=all`.

  `voicegw_request_ttfb_seconds` and `voicegw_request_total_latency_seconds` keep
  `# TYPE ... summary`: summary quantiles are sliding-window statistics, never
  counters, and no `_sum` / `_count` children are published. Their HELP text now
  names the same rolling 24-hour window. `voicegw_uptime_seconds`, the
  `*_configured` series and every `voicegw_diag_*` series were already gauges and
  are unchanged.

- **BREAKING for CI: `voicegw livekit check` exit codes changed.** This needs a
  MINOR version bump, not a patch. The command had **two** verdict
  implementations that disagreed (`livekit_diag/service.py:_verdict` for the
  dashboard, `livekit_diag/report.py:check_json` for the CLI). They are collapsed
  into the new `livekit_diag/gates.py`, and where they disagreed the stricter
  reading won. **If you run this command in CI, your pipeline may start failing
  on conditions it previously passed. That is the gate working, not a
  regression** — the old exit code was 0 for runs that had measured nothing at
  all.

  What now exits 1 that used to exit 0:

  - **No agent was in any room.** The latency gate had nothing to iterate, so
    neither implementation ever said it had not run, and the command reported a
    clean `PASS`. It now reports `UNKNOWN`.
  - **A probe measured nothing** (the agent never joined, or never replied).
    `summarize` returns a fabricated `0.0` average for zero samples, which is
    under any target; the dashboard verdict compared that 0.0 and passed. The CLI
    already warned here, so CLI users see no change on this case.
  - **The SFU baseline returned no connection quality.**

  There is a **new `UNKNOWN` verdict**, alongside `PASS` / `WARN` / `FAIL`. It
  means a gate could not be evaluated, which is not the same as healthy and is
  not the same as a measured problem. It exits 1. The verdict also appears on the
  dashboard's diagnostics runs (`GET /api/diagnostics/runs`), where a
  `Poor`/`Lost` SFU baseline is now `FAIL` rather than `WARN`, and an `sfu_load`
  baseline is judged at all (`_verdict` only ever read the `sfu` check).

  **Exit codes are still only 0 and 1.** WARN, FAIL and UNKNOWN all exit 1, so an
  existing `if [ $? -eq 1 ]` pipeline keeps catching everything it caught before.

- **`voicegw livekit check --strict`** is new. It gates the slowest measured turn
  instead of the average, which is what a caller who hung up actually
  experienced. Opt-in, because moving the default statistic would turn runs red
  for a reason unrelated to the collapse above. The failing line names the metric
  it thresholded, and names it honestly: with fewer than 10 samples it prints
  `agent_reply_latency_max_of_2_ms`, never `p95` (`check` probes twice, and
  `MAX_LATENCY_TRIALS` is 3, so a p95 is not a statistic this command can
  compute).

- **`check --json` gained a `gates` array** — one entry per gate with its status,
  the metric that decided it, the value and the threshold. Every pre-existing key
  in that payload is unchanged and in the same place.

- **Loss is no longer part of any verdict.** Both old implementations had a
  `loss_pct > 1.0` branch, and `sfu.py` hardcodes `loss_pct = 0.0` because
  per-connection loss is not exposed by the SDK. Those branches could never fire;
  gating on a constant is theatre. `quality` carries the connection signal that
  is real.

### Added

- **`voicegw livekit report`** exports a recorded diagnostics run as one
  self-contained HTML file, so CI can collect the artifact on a host that never
  runs the dashboard. It probes nothing and judges nothing: it reads a run out of
  the local store and reproduces the verdict that run recorded.

  The renderer moved out of the dashboard endpoint into
  `livekit_diag/run_report.py` and **both surfaces now call it**. The CLI's file
  and `GET /api/diagnostics/runs/{id}/report.html` are byte-identical for the
  same run on the same host, which a test asserts by comparing the bytes: two
  copies of a report renderer are two reports that disagree the first time one of
  them is edited.

  The document references nothing external (no script, stylesheet, font, image or
  CDN), so it renders offline from `file://`. `--json` emits the same payload the
  HTML is rendered from, still at `schema_version: 1` (the extraction changed no
  payload). A run that failed still exports, and reads as a run that measured
  nothing rather than one that measured zeros. It exits 0 whenever it wrote the
  artifact, whatever the verdict was: gating CI on a deployment's health stays
  `voicegw livekit check`'s job.

- **`workers.node_sample_max_age_days`** sets how long a raw `node_samples` row
  is kept. Default `7`, unchanged from when it was hardcoded, so upgrading moves
  nobody's retention.

  Raise it before running anything you intend to report on for longer than a
  week: retention equal to the observation window prunes the window's first day
  before the run ends, and the report then cannot cover the span the run was
  performed to demonstrate. Retention has to exceed the run, not match it.

  The cost is rows. One target at the default 15s interval writes 5,760 a day,
  so N targets over D days is roughly `N x 5760 x D`.

  **`workers` is `extra: forbid`**, so writing this key on an earlier version is
  a startup validation error rather than an ignored setting. The key has to ship
  before anyone puts it in `voicegw.yaml`.

### Removed

- **The `voicegw tui` terminal UI is gone.** The four-tab Textual UI (~4,900 LOC
  under `cli/tui/`, its 8 pilot tests, and the `textual` dependency) is removed.
  Its read paths are covered by the web dashboard (served by `voicegw serve`) and
  the `costs` / `logs` / `reconcile` CLI commands, which read local storage
  directly and so work even while the daemon is down. `voicegateway[dashboard]`
  no longer pulls `textual`.

### Fixed

- **`VOICEGW_COLLECTOR_URL` no longer double-appends `/v1/ingest`.** The value is
  the collector's base host; the engine appends `/v1/ingest` (and a full
  `.../v1/ingest` URL is now accepted too), fixing a silent 404 for anyone who
  followed the older docs that included the path.

### Changed

- **`onboard` and `smoke-test` are framework-agnostic now.** Both commands still
  assumed the removed config-driven provider/model pipeline. `onboard` asked you
  to pick a cloud provider and wrote its API key to `voicegw.yaml` in plaintext
  with no `models:` block; the old `smoke-test` then tried to exercise that
  pipeline, skipped everything (no models), and false-failed its session check.
  - **`onboard`** is now a four-question wizard (project, storage, port, daemon).
    It writes no `providers:` block and no key, prints the one line to add to
    your agent (`voicegateway.attach(session, project=...)`) plus the fleet
    collector env vars, and ends by running `check`.
  - **`smoke-test` is renamed to `check`** (the old name stays as a hidden,
    deprecated alias). `check` verifies the path that matters in the
    framework-agnostic model: it drives one synthetic instrumented request and
    asserts a request row + a session row land in storage. No providers, no
    network. It passes on a provider-less config, the case the old command got
    wrong.

### Fixed

- **The CLI no longer requires the livekit runtime to import.** The old
  `smoke_test` helper imported the instrumented middleware (which pulls livekit)
  at module load, so `voicegw` required livekit even for commands that do not use
  it. `check` imports that middleware lazily.
- **The daemon now serves the config you onboarded with.** The installed service
  ran `voicegw serve` with no `-c`, so it fell back to the config search path;
  when you onboarded to `~/.config/voicegateway/voicegw.yaml` (or an explicit
  path) the daemon could not find it, crash-looped under KeepAlive, and left
  nothing on the serve port (`doctor` reported "Daemon running: FAIL"). The
  daemon install now threads the onboarded config path into the launch command
  (`serve -c <path>`) across the macOS, Linux, and Windows backends.
- **Re-installing the daemon is idempotent.** On macOS a second
  `onboard --install-daemon` hit `launchctl bootstrap`'s EIO ("already loaded")
  and aborted, and a crash-looping daemon could be wedged in launchd's throttle
  state. Install now boots out any prior registration before bootstrapping the
  refreshed plist, so re-running onboard always lands the new config on a clean
  slate.
- **Extras reduced to four.** The optional-dependency groups collapse to
  `livekit`, `pipecat`, `dashboard`, and `collector`. The per-provider extras
  (`openai`, `deepgram`, `anthropic`, `groq`, `cartesia`, `elevenlabs`,
  `assemblyai`), the local-model extras (`whisper`, `kokoro`, `piper`), and the
  `server` / `tui` / `mcp` / `cloud` / `local` / `all` / `openrtc` groups are
  removed. VoiceGateway is framework-agnostic and no longer bundles provider or
  local-model wheels: it meters the native instances you build by `model_id`
  through voice-prices, so you install those plugins and runtimes in your own
  agent. `server`, the TUI (`textual`), and the MCP server fold into `dashboard`
  (the self-host tool surface); `postgres` and `duckdb` fold into `collector`.
  The `attach()` / `guard()` and provider-registry "not installed" errors now
  point at the upstream wheel (e.g. `livekit-plugins-openai`) instead of a
  VoiceGateway extra.

### Fixed

- **`voicegw` no longer crashes when the `dashboard` extra is absent.** The CLI
  eager-imported `serve_cli`, which imported FastAPI at module load, so any
  command (`onboard`, `doctor`, ...) failed with `ModuleNotFoundError: No module
  named 'fastapi'` on a non-server install. The server import is now lazy inside
  the `serve` command, which prints an install hint if the extra is missing.

## v0.15.0: OpenOrca fleet console + runtime intervention resolve

The `/openorca/*` runtime contract gains the pieces a live fleet console needs,
and the engine now serves that console itself at `/console`.

### Added

- **OpenOrca fleet console at `/console`.** A standalone React 19 + Tailwind v4
  SPA (built from `src/dashboard/console`) that renders `<OpenOrcaDashboard
  mode="runtime">` against this server's own `/openorca/*` endpoints. `voicegw
  serve` mounts it when built. Kept separate from the React 18 dashboard because
  `@openorca-ui/react` peers on React 19 and ships source Tailwind v4.
- **Real intervention-resolve endpoint.** `POST /openorca/interventions/resolve`
  validates the intervention id and action (`approve` / `deny` / `later`),
  tenant-scopes the request, persists the resolution with a TTL, and publishes a
  tenant-scoped `intervention.resolved` event. Resolved interventions drop out of
  the snapshot.
- **Snapshot enrichment.** The mapper folds live sessions into agent tasks and
  guardrail events into interventions per project, and rolls them into
  `fleetHealth`, so the snapshot reflects real call and guardrail state.

### Packaging

- The wheel and Docker image now build and bundle the console SPA
  (`build_wheel.sh` stages `_console_dist`; the Dockerfile adds a console builder
  stage), so `pip install voicegateway` and the published image both serve
  `/console`.

## v0.9.2: the Postgres fleet collector actually works

The Postgres collector backend carried several SQLite-only SQL constructs that
crashed the server on startup or on first ingest. They are fixed and now covered
by a CI job that boots the collector against a real Postgres.

### Fixed

- **Ambiguous `ON CONFLICT` columns.** The project and session upserts referenced
  bare existing-row columns (`COALESCE(excluded.x, x)`), which Postgres rejects as
  ambiguous between the target table and `excluded`. They are now table-qualified
  (`managed_projects.x`, `sessions.x`), which both SQLite and Postgres accept.
- **`GROUP_CONCAT` in the cost summary.** Replaced with the dialect-appropriate
  aggregate (`STRING_AGG` on Postgres, `GROUP_CONCAT` on SQLite).
- **`datetime('now', ...)` in the virtual-key staleness queries.** Replaced with a
  Postgres-compatible cutoff (`make_interval` / a Python-computed timestamp).

### Added

- **`Postgres collector` CI workflow.** A `dialect` job runs the collector against
  a Postgres service, and an `image-smoke` job builds the published image and
  boots `docker-compose.collector.yml` against Postgres. Together they gate the
  collector on a working Postgres path before release.

## v0.9.1: collector image and Postgres startup fixes

### Fixed

- **The core Docker image boots again.** It shipped without the hatch-vcs
  generated `_version.py` (the runtime copied the raw source over the installed
  package), so `import voicegateway` crashed on startup. The generated file is now
  baked into the image.
- **Postgres engine event-loop crash.** `Gateway.__init__` runs async startup
  through several short-lived `asyncio.run()` loops; asyncpg binds connections to
  their creating loop, so a pooled connection was reused across loops and crashed.
  The Postgres engine now uses `NullPool`.

## v0.9.0: per-session cost tracking for OpenRTC multi-agent workers

### Added

- **`voicegateway.openrtc.VoiceGatewayObserver`: one-line cost tracking for
  [OpenRTC](https://github.com/mahimailabs/openrtc) workers.** OpenRTC runs many
  LiveKit voice agents in a single worker process. This observer implements
  OpenRTC's `SessionObserver` protocol and drives `voicegateway.attach()` for
  every session, so a whole multi-agent worker gets per-call STT, LLM, and TTS
  cost tracking by passing one argument:
  `AgentPool(observers=[VoiceGatewayObserver(project="prod", collector_url=...,
  virtual_key=...)])`. Attribution is automatic per call: `agent_id` from the
  resolved agent name, `tenant_id` from room or job `metadata["tenant"]`, and
  `project` from the observer config. One sink is built lazily per worker and
  shared across all of that worker's sessions. The adapter is duck-typed (no hard
  runtime dependency on `openrtc`, so `import voicegateway.openrtc` works without
  it installed) and picklable for OpenRTC's `process` isolation mode. Install with
  `pip install "voicegateway[openrtc]"` (requires `openrtc>=0.3.0`). See the
  [OpenRTC example](https://docs.voicegateway.dev/examples/openrtc-multi-agent)
  for the full walkthrough.

## v0.8.6: minimal config records to storage so the dashboard works

### Fixed

- **`voicegw init` enables cost tracking by default again.** The v0.8.4 minimal
  template dropped the `cost_tracking` block, so `voicegw serve` started with
  storage disabled (`gateway.storage is None`) and the dashboard showed no costs,
  sessions, or agents, even though the agent SDK's `voicegateway.attach()` was
  writing to the default SQLite database. The minimal template now enables
  `cost_tracking` at that same default path
  (`~/.config/voicegateway/voicegw.db`), so a first run sees its agents on the
  dashboard with no extra wiring. Existing configs are unaffected; if your server
  was already started with storage off, add `cost_tracking: {enabled: true}` or
  set `VOICEGW_DB_PATH`.

## v0.8.5: fix the Docker image build

The published Docker images for v0.8.3 and v0.8.4 failed to build and were
never pushed. This restores them. The Python package is unchanged from v0.8.4.

### Fixed

- **The Docker image builds again.** v0.8.3 added a wheel `force-include` for the
  Alembic migrations, but neither Dockerfile copied `alembic/` and `alembic.ini`
  into the build stage, so `pip install` failed during metadata generation with
  `Forced include not found: /build/alembic`. Both the core and dashboard
  Dockerfiles now copy the migrations into the builder before installing. PyPI
  was unaffected (it uses a different build path); only the Docker images failed.

## v0.8.4: quieter agents, live dashboard version, friendlier init

Polish from dogfooding the agent SDK. Embedded telemetry no longer floods a
host agent's debug logs, the dashboard reports the real version, the model list
only shows models you can actually call, and `voicegw init` starts minimal.

### Fixed

- **Embedded storage no longer floods agent DEBUG logs.** `voicegateway.attach()`
  runs SQLite storage in-process. Under a LiveKit `console`/`dev` run (root
  logger at DEBUG) the `aiosqlite` and `alembic` loggers emitted a line per
  query, burying the agent's own output. `StorageService` now quiets those two
  dependency loggers to WARNING, and only when the caller has not set a level of
  their own (an explicit `aiosqlite=DEBUG` still wins).
- **The dashboard and `/health` report the real version.** The footer pill and
  the `/health` endpoint were hardcoded to `0.5.0`. Both now read the installed
  `__version__` (PEP 440 local build segment stripped), exposed via `/health`
  and the dashboard `/api/status`.

### Changed

- **The dashboard lists only callable models.** `/api/status` now returns models
  whose provider is configured (a cloud API key is set, or it is a local
  provider). The sidebar count and the Models page stop advertising models the
  operator cannot reach.
- **`voicegw init` writes a minimal config by default.** First run gets a short
  STT + LLM + TTS starter (about 35 lines) instead of the 269-line reference.
  Run `voicegw init --full` for the complete annotated config.

## v0.8.3: ship migrations in the wheel

### Fixed

- **The PyPI wheel now ships the Alembic migrations.** `alembic/` and
  `alembic.ini` live at the repo root, outside the `src/` packages, so the
  published wheel carried no migrations and `run_migrations()` failed at runtime
  with `alembic.ini not found` whenever storage initialized (hit by
  `voicegw serve`, `voicegw dashboard`, and `voicegateway.attach()`'s local
  SQLite sink). They are now force-included under the package, so a
  `pip install voicegateway` can build its schema on first run.

## v0.8.2: importable base install

### Fixed

- **`pip install voicegateway` is importable again.** SQLAlchemy and SQLModel
  are pulled in at import time by `voicegateway.models`, and the embedded
  storage that `voicegateway.attach()` writes through needs Alembic, but all
  three lived only in the `server` extra. They are now core dependencies, so a
  base install (the agent SDK use case) no longer fails with
  `ModuleNotFoundError: No module named 'sqlalchemy'`.
- **The `server` extra installs `python-multipart`.** FastAPI's dashboard logo
  upload (an `UploadFile` route) requires it; it was missing, so `voicegw serve`
  warned and the upload endpoint would have failed.

## v0.8.1: Docker fleet collector support

The official Docker image can now run as the Postgres-backed fleet collector.

### Fixed

- **The image ships the migrations.** `alembic.ini` and the `alembic/` tree are
  now copied into the image, so the server builds its schema on first start. It
  could not before (neither was copied in), which left storage broken on a fresh
  container for both SQLite and Postgres.
- **`VOICEGW_DB_URL` enables storage.** Pointing the collector at Postgres with
  `VOICEGW_DB_URL` alone now turns storage on. Previously it also required
  `cost_tracking.enabled` or `VOICEGW_DB_PATH`, so `POST /v1/ingest` returned
  503 and the collector persisted nothing.

### Added

- The Docker image includes the `postgres` extra (asyncpg), so it can run
  against a Postgres collector backend out of the box.
- `docker-compose.collector.yml`: a ready-to-run Postgres + collector stack,
  with a deployment guide in the docs.

## v0.8.0: fleet collector operational hardening

The self-hosted fleet collector becomes safe to run unattended: ingest rate
limiting, data retention, a windowed per-agent dashboard rollup, and the
background workers that keep them fresh. Two latent bugs that left the collector
fragile are fixed along the way.

### Added

- **Ingest rate limiting.** `POST /v1/ingest` enforces a per-caller token bucket
  (keyed by virtual key, then static API key, then client IP). Over-limit
  requests get `429` with a `Retry-After` header; oversized batches get `413`.
  Configured under the new `ingest` block (`requests_per_minute`, `burst`,
  `max_batch_size`).
- **Data retention.** A background worker hard-deletes aged rows per project:
  sessions and their dependent rows (replay, turns, dead-air, guardrail) by
  `ended_at`, and requests by `timestamp`, in batches. Configured under the new
  `retention` block (`default_days`, default 90).
- **Windowed fleet rollup.** A new `agent_observations` table and a 15-minute
  worker pre-aggregate per-agent cost, requests, p95, and error rate over a 24h
  window, so the Agents dashboard list is fast and internally consistent.
- **Background workers wired into the server.** The latency rollup, agent rollup,
  and retention workers now start with the collector. Configured under the new
  `workers` block (`enabled`, `rollup_interval_seconds`,
  `retention_interval_seconds`).

### Changed

- **Ingest rate limiting is on by default** (120 requests per minute per caller).
  A collector already ingesting faster will start receiving `429`s; the library's
  remote sink honors `Retry-After` and retries without dropping the batch. Set
  `ingest.requests_per_minute: 0` or `ingest.enabled: false` to opt out.
- **The Agents dashboard list now covers the last 24 hours** instead of all time.
  The JSON shape is unchanged; cost, requests, p95, and error rate are now
  window-scoped. The per-agent detail view stays all-time.

### Fixed

- **The remote sink no longer drops telemetry under rate limiting.** A `429` is
  treated as backpressure (parse `Retry-After`, clamp to 60s, retry the same
  batch) rather than dropped after a short fixed backoff.
- **Background workers now actually run in production.** The FastAPI lifespan was
  never attached, so the latency-rollup and retention workers were dormant; the
  collector now starts and stops all three workers on boot and shutdown.

## v0.7.0: voice-prices pricing backend

Pricing moves from `pydantic/genai-prices` to
[`voice-prices`](https://github.com/mahimailabs/voice-prices), a fork that
prices all three modalities (LLM, STT, and TTS) from one source.

### Changed

- **Single pricing backend.** LLM, STT, and TTS costs now all resolve
  through `voice-prices`. The hand-maintained local STT/TTS rate catalogs
  are retired; `voice-prices` owns rates and freshness (each entry carries
  `prices_checked` and `pricing_source_url`).
- **Pricing-source attribution.** Cloud-priced records are tagged
  `voice-prices@<version>`; self-hosted (`local/*`, `ollama/*`) models are
  tagged `voicegateway-local`; unknown models stay unpriced. The catalog-only
  `oldest_entry_date` field is dropped from the `/v1/status` and `/api/status`
  responses (`voice-prices` owns freshness).
- **STT and TTS rates** now follow `voice-prices` and may differ from the
  previous local-catalog estimates. Reconcile against your provider invoices.

### Dependencies

- `genai-prices` replaced by `voice-prices>=0.0.8,<0.1`.

## v0.6.0: first public release

The first public release of VoiceGateway. A self-hosted gateway for
LiveKit voice agents that tracks costs per modality (audio-minutes for
STT, tokens for LLM, characters for TTS) and reconciles logged costs
against provider invoices.

### What you get out of the box

- **Drop-in replacement for `livekit.agents.inference`.** Swap one
  import line and your agent code keeps running:
  `from voicegateway.inference import STT, LLM, TTS`. Cost tracking,
  latency monitoring, and per-session correlation happen transparently.
- **Cost tracking per modality.** LLM cost per 1k tokens (prices from
  `pydantic/genai-prices`, 1100+ models). STT cost per audio-minute and
  TTS cost per character (catalog with source-date metadata). Cached
  LLM input tokens are billed at the provider's cache-read discount
  rate (OpenAI 50%, Anthropic ~10%) by surfacing LiveKit's
  `prompt_cached_tokens` through to `genai-prices.cache_read_tokens`.
- **Background daemon.** `voicegw onboard` runs a five-question wizard,
  writes `voicegw.yaml`, registers a user-scoped service (LaunchAgent on
  macOS, `systemd --user` unit on Linux, Scheduled Task on Windows),
  and starts the daemon.
- **Web dashboard and HTTP API on a single port.** The daemon serves
  the React dashboard at `/`, the dashboard API at `/api/*`, and the
  public HTTP API at `/v1/*`. `voicegw dashboard` opens your browser
  at the daemon URL.
- **Reconciliation tooling.** `voicegw export-costs` and
  `voicegw reconcile` compare your logged costs against your provider's
  usage export. Per-row `pricing_source` attribution shows exactly
  which catalog or version priced each call.
- **MCP server for agent-managed config.** Seventeen tools over stdio
  and HTTP/SSE let Claude Code, Cursor, Codex, and Cline manage
  providers, projects, budgets, and queries conversationally.
- **Multi-tenant attribution.** Virtual API keys carry a tenant id so
  sessions auto-tag for per-customer reporting. Virtual keys expose
  their plaintext exactly once at creation and support soft revocation.
- **Cross-modality routing.** Per-session, lowest-predicted-total-
  latency selection of (STT, LLM, TTS) from per-project rosters, with
  observed latency feeding the predictor.
- **White-label branding.** Per-project logo, accent color, and
  product name. The dashboard chrome reflects the brand for users
  scoped to that project.
- **Conversation replay.** Per-modality time-ordered capture of every
  request, with retention windows configurable per project.
- **Guardrails.** Per-project policy overlay (PII categories, action
  enforcement), audit log of fired and bypassed events.

### Install

```bash
curl -fsSL https://voicegateway.dev/install.sh | bash
```

Or:

```bash
pipx install 'voicegateway[cloud,dashboard]'
uv tool install 'voicegateway[cloud,dashboard]'
```

See [Get started](https://docs.voicegateway.dev/get-started)
for the full first-run flow.
