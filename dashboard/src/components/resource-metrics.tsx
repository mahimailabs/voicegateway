import { useQuery } from "@tanstack/react-query"
import { Cpu } from "@phosphor-icons/react"
import { DataTable, type Columns } from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { QueryState } from "@/components/page-state"
import { SourceStatus, useObservationClock } from "@/components/source-status"
import { resourcesQuery, accessDenied } from "@/lib/api"
import { observedState } from "@/lib/live-status"

export function ResourceMetrics({ kind }: { kind: "sfu" | "worker" }) {
  const read = useQuery(resourcesQuery())
  const now = useObservationClock()
  if (!read.data || accessDenied(read.error)) return <QueryState icon={Cpu} what="container resources" query={read} />
  const targets = read.data.resources.filter((target) => target.kind === kind)
  const rows = targets.map((target) => {
    const sample = target.samples.at(-1)
    const observation = sample ? Date.parse(sample.observedAt) : NaN
    const status = observedState(sample ? Date.parse(sample.t) : null, target.sourceStatus, 15000, now, read.isError || read.fetchStatus === "paused")
    const state = status === "Observed active" && now - observation < 45000 ? "Current sample" : "Last observed"
    return { ...target, sample, state: sample ? state : "Awaiting samples" }
  })
  const columns: Columns<typeof rows[number]> = [
    { id: "target", header: "Container target", accessorFn: (r) => r.target },
    { id: "state", header: "Observation", accessorFn: (r) => r.state, cell: ({ row }) => <Badge variant="outline">{row.original.state}</Badge> },
    { id: "cpu", header: "Last CPU used", accessorFn: (r) => r.sample?.cpuUsed ?? null,
      cell: ({ row }) => row.original.sample?.cpuUsed == null ? "Unknown" : `${row.original.sample.cpuUsed.toFixed(2)} cores` },
    { id: "allocation", header: "CPU limit", accessorFn: (r) => r.sample?.cpuCores ?? null,
      cell: ({ row }) => row.original.sample?.cpuCores == null ? "Not reported" : `${row.original.sample.cpuCores.toFixed(2)} cores` },
    { id: "memory", header: "Memory working set", accessorFn: (r) => r.sample?.memoryBytes ?? null,
      cell: ({ row }) => row.original.sample?.memoryBytes == null ? "Unknown" : `${(row.original.sample.memoryBytes / 1048576).toFixed(1)} MiB` },
    { id: "memoryLimit", header: "Memory limit", accessorFn: (r) => r.sample?.memoryLimitBytes ?? null,
      cell: ({ row }) => row.original.sample?.memoryLimitBytes == null ? "Not reported" : `${(row.original.sample.memoryLimitBytes / 1048576).toFixed(1)} MiB` },
    { id: "observed", header: "Measured at", accessorFn: (r) => r.sample?.observedAt ?? null,
      cell: ({ row }) => row.original.sample ? new Date(row.original.sample.observedAt).toLocaleTimeString() : "Unknown" },
  ]
  return <section className="mx-auto max-w-[1200px] space-y-3" aria-label={`${kind === "sfu" ? "SFU" : "Worker"} container resources`}>
    <h2 className="text-lg font-medium">Container resources</h2>
    <DataTable data={rows} columns={columns} getRowId={(r) => r.id} urlPrefix={`${kind}resources`} empty="No resource exporter configured. CPU and memory are unavailable." />
    {targets.map((target) => <SourceStatus key={target.id} name={`${target.target} · ${target.host}`} source={target.sourceStatus} interval={15000} query={read} />)}
    <p className="text-xs text-muted-foreground">Container measurements include child processes. CPU used is separate from worker load. An unreported limit may mean unlimited allocation.</p>
  </section>
}
