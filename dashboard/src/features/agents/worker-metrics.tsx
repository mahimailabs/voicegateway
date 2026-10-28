import { useQuery } from "@tanstack/react-query"
import { Robot } from "@phosphor-icons/react"
import { DataTable, type Columns } from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { QueryState } from "@/components/page-state"
import { SourceStatus, useObservationClock } from "@/components/source-status"
import { workersQuery, accessDenied } from "@/lib/api"
import { observedState } from "@/lib/live-status"

export function WorkerMetrics() {
  const read = useQuery(workersQuery())
  const now = useObservationClock()
  if (!read.data || accessDenied(read.error)) return <QueryState icon={Robot} what="worker metrics" query={read} />
  const data = read.data
  const disconnected = read.isError || read.fetchStatus === "paused"
  const rows = data.workers.map((worker) => {
    const sample = worker.samples.at(-1)
    const state = observedState(sample ? Date.parse(sample.t) : null, worker.sourceStatus, 15000, now, disconnected)
    return { ...worker, sample, state: state === "Observed active" ? "Current sample" : state === "No longer listed" ? "Not in latest sample" : state }
  })
  const columns: Columns<typeof rows[number]> = [
    { id: "worker", header: "Worker", accessorFn: (r) => r.nodename },
    { id: "host", header: "Endpoint host", accessorFn: (r) => r.host },
    { id: "state", header: "Observation", accessorFn: (r) => r.state, cell: ({ row }) => <Badge variant="outline">{row.original.state}</Badge> },
    { id: "load", header: "Last worker load", accessorFn: (r) => r.sample?.load ?? null,
      cell: ({ row }) => row.original.sample?.load == null ? "Unknown" : `${(row.original.sample.load * 100).toFixed(0)}%` },
    { id: "jobs", header: "Last active jobs", accessorFn: (r) => r.sample?.activeJobs ?? null,
      cell: ({ row }) => row.original.sample?.activeJobs ?? "Unknown" },
    { id: "seen", header: "Last observed", accessorFn: (r) => r.sample?.t ?? null,
      cell: ({ row }) => row.original.sample ? new Date(row.original.sample.t).toLocaleTimeString() : "Unknown" },
  ]
  return <section className="mx-auto max-w-[1200px] space-y-3" aria-label="Worker metrics">
    <h2 className="text-lg font-medium">Worker metrics</h2>
    <SourceStatus name="Prometheus" source={data.sourceStatus} interval={15000} query={read} />
    <DataTable data={rows} columns={columns} getRowId={(r) => r.id} urlPrefix="workers" empty="No worker samples. Configure a project worker metrics endpoint." />
    {data.endpoints?.map((endpoint) => <SourceStatus key={endpoint.id} name={endpoint.host} source={endpoint.sourceStatus} interval={15000} query={read} />)}
    <p className="text-xs text-muted-foreground">Load is the worker’s configured load function, not CPU utilization. Last values remain visible during an outage; absence from a newer sample does not prove a process exited.</p>
  </section>
}
