# Live operations: container resource telemetry

## Audit

LiveKit Agents 1.8.7 exports worker load, active jobs and child-process count. Its
multiprocess Prometheus registry does not expose the default process collector. Worker load
is configurable and is not CPU utilization. Parent-process RSS would omit agent job processes.
LiveKit Server 1.13.3 computes internal node statistics, but ListRooms does not return them.

Use cAdvisor's maintained Prometheus collector for containerized SFU and agent-worker resources.
This includes the container's child processes. Do not install an exporter silently on a monitored
host. Administrators explicitly register an exporter and an exact container ID for each target.
Bare-process deployments without a resource exporter and LiveKit Cloud host metrics remain
unavailable, with an explicit source limitation. Do not manufacture allocations or regions.

## Implementation contract

- Project-scoped resource endpoints bind an encrypted exporter URL and exact container ID to an
  SFU or worker target. Reuse existing endpoint encryption, SSRF checks and bounded reads.
- Parse cAdvisor's container_cpu_usage_seconds_total, container_memory_working_set_bytes,
  container_spec_cpu_quota, container_spec_cpu_period, container_spec_memory_limit_bytes,
  container_start_time_seconds and container_last_seen using Prometheus expfmt.
- CPU cores used is the CPU counter delta divided by sample elapsed seconds. The first sample,
  counter reset, container restart, missing counter or stale source cannot produce a rate.
  A configured quota divided by period gives allocated CPU cores. CPU percent requires this
  positive allocation; CPU shares are not an allocation.
- Zero/unlimited memory limits do not become a zero-byte allocation. Memory working set and
  memory limit are independent nullable measurements. Do not sum parent and child cgroups.
- Exact container matching is required. Ambiguous CPU series, missing targets and invalid
  measurements cannot appear as healthy zero usage. Use resource metric timestamps, not container_last_seen, which cAdvisor sets to the scrape time. Total CPU series take precedence; otherwise sum unique per-CPU series without mixing them with a total.
- Worker stores resource samples in ClickHouse and publishes the existing scoped live hint only
  after durable storage. Web reads snapshots. Failure retains timestamped prior observations.
- Preserve the approved pages. Expose resource usage, allocation and source freshness with
  explicit container scope, separate from worker load and native job counts.

## Acceptance

1. Exact mapping isolates targets and projects; credentials are sealed and absent from responses.
2. Real exporter samples yield CPU and memory; first sample/reset/stale/missing are unknown.
3. Container limits, unlimited resources and child-process usage have correct semantics.
4. Disconnect, absence and recovery remain distinct and observable.
5. Authentication, permissions and SSRF protections cover configuration and reads.
6. Native container verification, meaningful failure tests, full checks and review pass before
   this work is marked complete. The parser alone does not complete this outcome.

## Source evidence

- livekit/agents, tag livekit-agents@1.8.7: telemetry/metrics.py and telemetry/http_server.py.
- livekit/livekit, tag v1.13.3: pkg/telemetry/prometheus/node.go and pkg/routing/nodestats.go.
- google/cadvisor, tag v0.60.6: lib/metrics/prometheus.go. CPU exports either total or per-CPU series; usage metrics carry the underlying stats timestamp. container_last_seen is exporter time, not the underlying measurement time. Memory limits above 2^62 are exported as zero (unlimited).

## Verification record (2026-10-20)

- Native cAdvisor v0.60.6 on a temporary Docker container passed the collector -> ClickHouse ->
  authenticated API check. The measured sample reported 0.001804 CPU cores, a 0.5-core quota,
  and 11,915,264 bytes working set against a 67,108,864-byte limit. Both temporary containers
  were removed. This proves the container path, not access to LiveKit Cloud host resources.
- Targeted real Postgres/ClickHouse persistence, parser, collector failure/recovery and API
  permission tests passed. The dashboard's 205 tests passed with bounded test concurrency.
- Full acceptance remains pending: the first full run exposed a live-feed test using a
  900-microsecond HTTP collection window and an auth suite timeout under Docker contention.
  The test now uses the production collection interval and explicitly resets its test lease
  for the manual second collection. The full race-detector rerun passed with two package workers, followed by all 205 dashboard tests. Lint, TypeScript and API checks passed.
- Browser verification initially passed 71 of 72 checks; the remaining call drawer timed out
  during concurrent Docker tests. The same check failed in a second parallel run, then passed three isolated runs without changing assertions or timeouts. The complete serial browser suite and hosted checks remain pending before completion.
