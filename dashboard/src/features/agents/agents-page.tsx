import { useMemo, useState } from "react"
import { CaretRight, CircleHalf, Cloud, HardDrives, Info, Robot, ShieldWarning, Timer, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { BarList } from "@/components/tremor/BarList"
import { CategoryBar } from "@/components/tremor/CategoryBar"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { capacityQuery, cloudAgentsQuery, mediaServerQuery } from "@/lib/api"
import type { CloudAgents } from "@/lib/api-types"
import { type MediaServerData, type Room, RoomDrawer, fromContract, regionCity } from "../media-server/room-drawer"
import { AgentDrawer, type AgentReads } from "./agent-drawer"
import { CapacityButton, CapacityDialog } from "./capacity"
import { type Agent, type Check, agentsFrom, checks, cores, gib, joinDelays, median } from "./agents-model"
import { useUrlPick, useUrlState } from "@/lib/url-state"

// Agent workers as the project's three keys see them (decision 0016): every agent that is deployed to
// LiveKit Cloud or that took a job in a room we watched, its state and resources where LiveKit reports
// them, the jobs it took, and what is wrong. Self-hosted workers have no listing API, so they appear
// only through their jobs; their load needs their own metrics endpoint (Phase 2).

const tone = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/40" }
const statusTone = (s: string | null) =>
  !s
    ? "bg-muted text-muted-foreground"
    : ["Running"].includes(s)
      ? "bg-primary/10 text-primary"
      : ["Sleeping", "Waking"].includes(s)
        ? "bg-muted text-muted-foreground"
        : ["Error", "CrashLoop", "Disabled", "Build Failed", "Server Error", "Suspended", "Deleted"].includes(s)
          ? "bg-destructive/10 text-destructive"
          : "bg-amber-500/10 text-amber-700 dark:text-amber-400"

function StatCard({ icon: I, value, label, children, alert }: { icon: Icon; value: React.ReactNode; label: string; children?: React.ReactNode; alert?: boolean }) {
  return (
    <div className="rounded-2xl border bg-background p-4">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">{label}</p>
        <I size={18} className={alert ? "text-destructive" : "text-muted-foreground"} />
      </div>
      <p className={cn("mt-2 text-[28px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</p>
      {children && <div className="mt-3">{children}</div>}
    </div>
  )
}

// Bar for the fleet table: in use, then reserved, then up to the limit.
function MiniBar({ cur, req, limit, unit }: { cur: number | null; req: number | null; limit: number | null; unit: string }) {
  if (limit === null) return <span className="text-[12px] text-muted-foreground">unknown</span>
  const fmt = (v: number) => Number(v.toFixed(2)).toString()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="cursor-help">
          <CategoryBar values={[cur ?? 0, Math.max((req ?? 0) - (cur ?? 0), 0), Math.max(limit - Math.max(req ?? 0, cur ?? 0), 0)]} colors={["accent", "quiet", "faint"]} showLabels={false} />
          <p className="mt-1 text-[11px] text-muted-foreground tnum">{`${cur === null ? "?" : fmt(cur)} of ${fmt(limit)} ${unit}`}</p>
        </div>
      </TooltipTrigger>
      <TooltipContent className="text-xs">{`In use ${cur === null ? "unknown" : fmt(cur)}, reserved ${req === null ? "unknown" : fmt(req)}, limit ${fmt(limit)} ${unit}`}</TooltipContent>
    </Tooltip>
  )
}

const failedJobs = (a: Agent) => a.jobs.filter((j) => j.failed).length
const right = { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" }
function agentColumns(onTest: (a: Agent) => void): Columns<Agent> {
  return [
    {
      id: "name",
      accessorFn: (a) => a.name ?? "",
      header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
      cell: ({ row: { original: a } }) => (
        <span className="flex items-start gap-2">
          <span>
            <span className="block text-[13px] font-medium">{a.name ?? "Unnamed agent"}</span>
            <span className="block font-mono text-[11px] text-muted-foreground">{a.cloud?.id ?? `${a.workers.length || "no"} ${a.workers.length === 1 ? "worker" : "workers"} seen`}</span>
          </span>
        </span>
      ),
    },
    {
      id: "runs",
      accessorFn: (a) => (a.cloud ? "cloud" : "self"),
      header: ({ column }) => <DataGridColumnHeader title="Runs on" column={column} />,
      cell: ({ row: { original: a } }) => (
        <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          {a.cloud ? <Cloud size={14} /> : <HardDrives size={14} />}
          {a.cloud ? `LiveKit Cloud, ${a.cloud.deployment?.region ?? "?"}` : `not deployed here, ${a.regions.map((r) => regionCity[r] ?? r).join(", ") || "?"}`}
        </span>
      ),
    },
    {
      id: "status",
      accessorFn: (a) => a.cloud?.deployment?.status ?? "",
      header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
      cell: ({ row: { original: a } }) => {
        const st = a.cloud?.deployment?.status
        return st ? <span className={cn("rounded-full px-2 py-0.5 text-[12px]", statusTone(st))}>{st}</span> : <span className="text-[12px] text-muted-foreground">not reported</span>
      },
    },
    {
      id: "cpu",
      header: "CPU",
      enableSorting: false,
      size: 160,
      cell: ({ row: { original: a } }) => {
        const d = a.cloud?.deployment
        return d ? <MiniBar cur={cores(d.curCpu)} req={cores(d.cpuReq)} limit={cores(d.cpuLimit)} unit="cores" /> : <span className="text-[12px] text-muted-foreground">unknown</span>
      },
    },
    {
      id: "memory",
      header: "Memory",
      enableSorting: false,
      size: 160,
      cell: ({ row: { original: a } }) => {
        const d = a.cloud?.deployment
        return d ? <MiniBar cur={gib(d.curMem)} req={gib(d.memReq)} limit={gib(d.memLimit)} unit="GiB" /> : <span className="text-[12px] text-muted-foreground">unknown</span>
      },
    },
    { id: "jobs", accessorFn: (a) => a.jobs.length, header: ({ column }) => <DataGridColumnHeader title="Jobs" column={column} />, meta: right, cell: ({ row }) => <span className="text-[13px] tnum">{row.original.jobs.length}</span> },
    {
      id: "failed",
      accessorFn: failedJobs,
      header: ({ column }) => <DataGridColumnHeader title="Failed" column={column} />,
      meta: right,
      cell: ({ row }) => <span className={cn("text-[13px] tnum", failedJobs(row.original) > 0 && "text-destructive")}>{failedJobs(row.original)}</span>,
    },
    {
      id: "join",
      accessorFn: (a) => median(joinDelays(a.jobs)) ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Join" column={column} />,
      meta: right,
      cell: ({ row }) => {
        const ms = median(joinDelays(row.original.jobs))
        return <span className={cn("text-[13px] tnum", ms !== null && ms > 2 && "text-amber-700 dark:text-amber-400")}>{ms === null ? "unknown" : `${ms.toFixed(1)} s`}</span>
      },
    },
    {
      id: "test",
      header: () => null,
      size: 48,
      enableSorting: false,
      cell: ({ row }) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <CapacityButton label={false} onClick={() => onTest(row.original)} />
            </span>
          </TooltipTrigger>
          <TooltipContent className="text-xs">Test capacity</TooltipContent>
        </Tooltip>
      ),
    },
    { id: "go", header: () => null, size: 32, enableSorting: false, cell: () => <CaretRight className="text-muted-foreground" /> },
  ]
}
const agentFields: Field<Agent>[] = [
  {
    id: "runs",
    label: "Runs on",
    type: "select",
    icon: <Cloud />,
    options: [
      { value: "cloud", label: "LiveKit Cloud" },
      { value: "self", label: "Not deployed here" },
    ],
    get: (a) => (a.cloud ? "cloud" : "self"),
  },
  { id: "status", label: "Status", type: "text", icon: <CircleHalf />, get: (a) => a.cloud?.deployment?.status ?? null },
  { id: "failed", label: "Failed jobs", type: "number", icon: <WarningCircle />, get: failedJobs },
  { id: "join", label: "Join (s)", type: "number", icon: <Timer />, get: (a) => median(joinDelays(a.jobs)) },
]

export function AgentsPage() {
  const cloud = useQuery(cloudAgentsQuery())
  const media = useQuery(mediaServerQuery())
  // The capacity run is optional: when it cannot be read the drawer says so.
  const run = useQuery(capacityQuery())
  const ms = useMemo(() => (media.data ? fromContract(media.data) : null), [media.data])
  if (!cloud.data) return <QueryState icon={Robot} what="the agents" query={cloud} />
  if (!ms) return <QueryState icon={Robot} what="the agents' rooms" query={media} />
  // Only a read that answered can say "not measured"; while loading or after a failure the drawer says which.
  const runNote = run.isError ? "The capacity run could not be loaded." : "Loading the capacity run."
  return <AgentsView cloud={cloud.data} ms={ms} run={run.isSuccess ? run.data : undefined} runNote={runNote} />
}

function AgentsView({ cloud, ms, run, runNote }: { cloud: CloudAgents; ms: MediaServerData; run: AgentReads["run"]; runNote: string }) {
  const { source, readAt } = cloud
  const [tab, setTab] = useUrlState("tab", "fleet")
  const list = useMemo(() => agentsFrom(cloud, ms), [cloud, ms])
  const found = checks(list)
  const [openRoom, setOpenRoom] = useState<Room | null>(null)
  // The open agent is in the URL (?agent=<key>), which is also how the command palette opens one.
  const [openAgent, setOpenAgent] = useUrlPick<Agent>("agent", list, (a) => a.key)
  const [testing, setTesting] = useUrlPick<Agent>("capacityAgent", list, (a) => a.key)
  const columns = useMemo(() => agentColumns(setTesting), [setTesting])
  const jobs = list.flatMap((a) => a.jobs)
  const timed = jobs.filter((j) => Number.isFinite(j.dispatchedMs) && j.joinedMs !== null)
  const untimed = jobs.length - timed.length
  const joinBars = timed.map((j) => ({
    key: `${j.room.sid}${j.jobId}`,
    name: `Room ${j.room.n} · ${list.find((a) => a.jobs.includes(j))?.name ?? "agent"}${j.failed ? " · failed, retried" : ""}`,
    value: (j.joinedMs! - j.dispatchedMs) / 1000,
  }))
  const failed = jobs.filter((j) => j.failed).length
  const delays = joinDelays(jobs)
  const m = median(delays)
  const cloudN = list.filter((a) => a.cloud).length
  const sev = (s: Check["severity"]) => found.filter((c) => c.severity === s).length

  return (
    <div className="mx-auto max-w-[1200px] space-y-12">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Agent workers</h1>
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" aria-label="How this was read" className="text-muted-foreground hover:text-foreground">
                <Info size={18} />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-96 text-xs leading-relaxed">{`${source} Read ${new Date(readAt).toLocaleString()}.`}</TooltipContent>
          </Tooltip>
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <StatCard icon={Robot} value={list.length} label="Agents">
            <p className="text-[11px] text-muted-foreground">{`${cloudN} deployed on LiveKit Cloud, ${list.length - cloudN} seen only in rooms`}</p>
          </StatCard>
          <StatCard icon={WarningCircle} value={`${failed} of ${jobs.length}`} label="Jobs failed" alert={failed > 0}>
            <div className="flex gap-0.5">
              {jobs.map((j) => (
                <span key={`${j.room.sid}${j.jobId}`} className={cn("h-2 flex-1 rounded-sm", j.failed ? "bg-destructive" : "bg-primary/60")} />
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">one bar per job watched</p>
          </StatCard>
          <StatCard icon={Timer} value={m === null ? "Unknown" : `${m.toFixed(1)} s`} label="Dispatch to join">
            <p className="text-[11px] text-muted-foreground">{m === null ? "No job with dispatch detail yet" : `median of ${delays.length} jobs, LiveKit dispatches in under 150 ms`}</p>
          </StatCard>
          <StatCard icon={ShieldWarning} value={found.length} label="Checks to fix" alert={found.some((c) => c.severity === "high")}>
            <CategoryBar values={[sev("high"), sev("medium"), sev("low")]} colors={["alert", "amber", "quiet"]} showLabels={false} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${sev("high")} high · ${sev("medium")} medium · ${sev("low")} low`}</p>
          </StatCard>
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList>
          <TabsTrigger value="fleet">Fleet</TabsTrigger>
          <TabsTrigger value="join">Join time</TabsTrigger>
          <TabsTrigger value="checks">
            Checks <span className="ml-1 rounded-full bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{found.length}</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="fleet">
          <DataTable data={list} columns={columns} fields={agentFields} getRowId={(a) => a.key} onRowClick={setOpenAgent} empty="No agent matches these filters." />
          <p className="mt-2 text-[12px] text-muted-foreground">Open an agent for calls at once, resources, join times, versions and its jobs.</p>
        </TabsContent>

        <TabsContent value="join" className="space-y-3">
          <p className="text-[13px] text-muted-foreground">Time from LiveKit dispatching the job to the agent joining the room, per job. The caller hears nothing in this gap.</p>
          <div className="rounded-xl border p-4">
            <BarList data={joinBars} valueFormatter={(v) => `${v.toFixed(1)} s`} sortOrder="none" />
          </div>
          {untimed > 0 && <p className="text-[12px] text-muted-foreground">{`${untimed} more jobs have no timing: that watch kept no dispatch detail.`}</p>}
        </TabsContent>

        <TabsContent value="checks">
          <ul className="divide-y rounded-xl border">
            {found.map((c, k) => (
              <li key={k}>
                <Collapsible>
                  <CollapsibleTrigger className="group flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50">
                    <span className={cn("size-2 shrink-0 rounded-full", tone[c.severity])} />
                    <span className="flex-1 text-[14px]">{c.title}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">{c.where}</span>
                    <CaretRight size={14} className="text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
                  </CollapsibleTrigger>
                  <CollapsibleContent className="space-y-1.5 px-4 pb-3 pl-9 text-[13px]">
                    <p className="text-muted-foreground">{c.why}</p>
                    <p>
                      <span className="font-medium">Fix: </span>
                      {c.fix}{" "}
                      <a href={c.doc} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                        Docs
                      </a>
                    </p>
                  </CollapsibleContent>
                </Collapsible>
              </li>
            ))}
          </ul>
        </TabsContent>
      </Tabs>

      <AgentDrawer agent={openAgent} reads={{ ms, readAt, run, runNote }} onOpenChange={(o) => !o && setOpenAgent(null)} onTest={(a) => (setOpenAgent(null), setTesting(a))} />
      <CapacityDialog agent={testing} ms={ms} onOpenChange={(o) => !o && setTesting(null)} />
      <RoomDrawer room={openRoom} ms={ms} open={openRoom !== null} onOpenChange={(o) => !o && setOpenRoom(null)} />
    </div>
  )
}
