# Container CPU and memory

VoiceGateway reads cAdvisor metrics for explicitly mapped SFU and agent-worker containers.
Measurements include child processes inside that container. They are not host-wide utilization
and do not replace LiveKit's configurable worker load or active job count.

Run cAdvisor on the monitored Docker host using its official deployment instructions. The verified
version is 0.60.6, image
`ghcr.io/google/cadvisor:0.60.6@sha256:b8e7d1093144fd088f425ff003d75a4aa405de075db78dae3bc563730b1bd07a`.
Only make its metrics endpoint reachable from the VoiceGateway worker. The exporter requires
privileged access to host container metadata. Do not publish it to the internet. On Docker Desktop,
the verified setup also uses host cgroup namespace and the containerd socket under the mounted
host root (`--containerd=/rootfs/run/containerd/containerd.sock`), plus an explicit Docker socket mount.
Other hosts should use their actual containerd socket, not assume this path.

Read the exact `id` label from the target container's metrics. Do not use a container display name,
the root cgroup, or a prefix. Register the mapping as an installation administrator:

```sh
voicegateway project resources add --org default --project production --kind worker --target worker-a --container /docker/EXACT_CONTAINER_ID
```

Supply the exporter URL through `VOICEGATEWAY_RESOURCE_METRICS_URL` when it contains credentials,
so the URL is absent from the command line and shell history. Configuration requires the same
Postgres and secret-key environment as the other project commands. URLs are encrypted at rest and
never returned by the read API. Use `--kind sfu` for media-server containers. Each project can have
only one mapping per kind and target. Remove and recreate a mapping when its container ID changes.

```sh
voicegateway project resources list --org default --project production
voicegateway project resources remove RESOURCE_ID --org default --project production
```

The worker samples every 15 seconds. The existing Media server and Agent workers pages display
resource tables; project viewers can read them, but configuration is installation-admin CLI only.

- CPU used is CPU seconds divided by elapsed measurement time, expressed in cores. The first
  sample, restart, counter reset or gap over 45 seconds has no CPU rate.
- CPU limit is quota divided by period. CPU shares are not an allocation.
- Memory is the working set, not RSS of only the parent process. Unlimited/unreported limits
  stay null. A missing measurement never becomes zero.
- Values carry cAdvisor's measurement timestamp. A healthy exporter cannot freshen stale values.
  Failed reads retain the last observation and display the source failure.
- A worker outside a mapped container and LiveKit Cloud host resources remain unavailable. The
  console does not infer resource usage from worker load or claim access to hosted infrastructure.

## Native verification

The optional `TestNativeResourceExporterToAPI` test takes `VG_TEST_RESOURCE_URL` and
`VG_TEST_RESOURCE_CONTAINER`. Point these at a temporary cAdvisor exporter and a test container
limited to 0.5 CPU and 64 MiB. Run with `CI=1 go test -race ./internal/api -run
TestNativeResourceExporterToAPI -v`. It checks the real exporter, collector, ClickHouse and scoped
API. It does not provision privileged exporters automatically. Normal tests separately exercise
real Postgres/ClickHouse, HTTP failures, permissions, stale data and recovery.
