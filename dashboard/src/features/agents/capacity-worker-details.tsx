import { Info } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

export function CapacityWorkerDetails({ index, jobs }: { index: number; jobs: number }) {
  const fields = [
    ["CPU allocation", "2 vCPU"],
    ["Memory limit", "4 GiB"],
    ["CPU utilization", `${Math.round(12 + jobs * 11)}%`],
    ["Memory used", `${(0.6 + jobs * 0.24).toFixed(1)} GiB`],
    ["Active jobs", String(jobs)],
    ["Worker load", `${jobs * 16}%`],
    ["Region", "us-east-1"],
    ["Runtime", "Python 3.13 · Agents 1.8.7"],
  ]
  return <Tooltip delayDuration={150}>
    <TooltipTrigger asChild><button type="button" className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring rounded-sm" aria-label={`Worker ${index + 1} specifications`}>Worker {index + 1}<Info size={12} /></button></TooltipTrigger>
    <TooltipContent side="top" sideOffset={8} className="block w-80 max-w-[calc(100vw-2rem)] space-y-3 rounded-xl bg-popover p-4 text-popover-foreground shadow-md ring-1 ring-foreground/10 [&>svg]:hidden">
      <div className="flex items-center justify-between gap-2"><span className="font-semibold">Worker {index + 1}</span><Badge variant="secondary">Example specs</Badge></div>
      <dl className="space-y-2 border-y py-3">{fields.map(([label, value]) => <div key={label} className="flex justify-between gap-4 text-xs"><dt className="text-muted-foreground">{label}</dt><dd className="text-right tabular-nums">{value}</dd></div>)}</dl>
      <p className="text-[11px] text-muted-foreground">Illustrative allocation and usage. Worker load is separate from CPU utilization.</p>
    </TooltipContent>
  </Tooltip>
}
