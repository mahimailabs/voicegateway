import { useState } from "react"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { ArrowLeft, Cloud, HardDrives, WarningCircle } from "@phosphor-icons/react"
import { BarList } from "@/components/tremor/BarList"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import type { CapacityRun } from "@/lib/api-types"
import { type MediaServerData, type Room, RoomBody, openAtWatchEnd, regionCity } from "../media-server/room-drawer"
import { type Agent, cores, gib, joinDelays, median, reportedTime } from "./agents-model"
import { CapacityButton, CapacityResult, capacityRun } from "./capacity"

// One agent, everything the three keys and the watched rooms say about it. Calls at once is counted
// from the agent's own time in each room (join to last seen), per watch: between watches nothing was
// read, so the gaps are not drawn as zero.

const at = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
const day = (s: number) => new Date(s * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
const when = reportedTime
const config = { calls: { label: "Calls at once", color: "var(--chart-1)" } } satisfies ChartConfig

type Span = { from: number; to: number; open: boolean; room: Room }
function spans(a: Agent, ms: MediaServerData): Span[] {
  // Every time the agent was in a room counts, including a second join after LiveKit retried a job.
  const rooms = [...new Map(a.jobs.map((j) => [j.room.sid, j.room])).values()]
  return rooms.flatMap((room) =>
    room.participants
      .filter((x) => x.kind === "agent" && (x.agentName ?? "unnamed") === (a.name ?? "unnamed"))
      // A stay with no reported start or end cannot be counted on the chart.
      .flatMap((p) => {
        const from = p.joinedAtMs !== null ? p.joinedAtMs / 1000 : p.firstSeen
        return from !== null && p.lastSeen !== null ? [{ from, to: p.lastSeen, open: openAtWatchEnd(p, ms), room }] : []
      }),
  )
}

function windows(list: Span[], data: MediaServerData) {
  return data.windows
    .filter((w): w is typeof w & { start: number; end: number } => w.start !== null && w.end !== null)
    .map((w) => {
      const inside = list.filter((s) => s.to >= w.start && s.from <= w.end)
      if (!inside.length) return null
      const edges = [...new Set([w.start, w.end, ...inside.flatMap((s) => [Math.max(s.from, w.start), Math.min(s.to, w.end)])])].sort((x, y) => x - y)
      const series = edges.map((t) => ({ t, time: at(t), calls: inside.filter((s) => s.from <= t && s.to > t).length }))
      return { w, series, peak: Math.max(...series.map((x) => x.calls)), open: inside.some((s) => s.open) }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
}

function Stat({ label, value, note, alert }: { label: string; value: React.ReactNode; note?: string; alert?: boolean }) {
  return (
    <div className="rounded-xl border p-3">
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-[22px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</p>
      {note && <p className="mt-1.5 text-[11px] text-muted-foreground">{note}</p>}
    </div>
  )
}

function Meter({ label, cur, req, limit, unit }: { label: string; cur: number | null; req: number | null; limit: number | null; unit: string }) {
  const max = limit ?? req
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-[12px]">
        <span className="text-muted-foreground">{label}</span>
        <span className="tnum">{cur === null || max === null ? "unknown" : `${cur.toFixed(2)} of ${max} ${unit}`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">{cur !== null && max ? <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(cur / max, 1) * 100}%` }} /> : null}</div>
      {req !== null && limit !== null && <p className="text-[11px] text-muted-foreground tnum">{`requests ${req}, limit ${limit} ${unit}`}</p>}
    </div>
  )
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-[14px] font-semibold">{title}</h3>
        {note && <p className="text-[12px] text-muted-foreground">{note}</p>}
      </div>
      {children}
    </section>
  )
}

// A room opens in place of the agent, with a way back, rather than as a second drawer on top.
// reads: the media server view, when the CloudAgent API was read, and the project's capacity run
// (undefined when it could not be read, so "not measured" is never claimed for it).
export type AgentReads = { ms: MediaServerData; readAt: string; run: CapacityRun | null | undefined; runNote: string }
export function AgentDrawer({ agent: a, reads, onOpenChange, onTest }: { agent: Agent | null; reads: AgentReads; onOpenChange: (o: boolean) => void; onTest: (a: Agent) => void }) {
  const [room, setRoom] = useState<Room | null>(null)
  return (
    <Sheet
      open={a !== null}
      onOpenChange={(o) => {
        if (!o) setRoom(null)
        onOpenChange(o)
      }}
    >
      <SheetContent side="right" onOpenAutoFocus={(e) => e.preventDefault()} className="gap-0 overflow-y-auto p-0 data-[side=right]:w-[min(880px,92vw)] data-[side=right]:sm:max-w-none">
        {a && room ? (
          <>
            <div className="border-b px-4 py-2">
              <Button variant="ghost" size="sm" onClick={() => setRoom(null)}>
                <ArrowLeft /> Back to {a.name ?? "the agent"}
              </Button>
            </div>
            <RoomBody room={room} ms={reads.ms} />
          </>
        ) : (
          a && <Body a={a} reads={reads} onRoom={setRoom} onTest={() => onTest(a)} />
        )}
      </SheetContent>
    </Sheet>
  )
}

function Body({ a, reads, onRoom, onTest }: { a: Agent; reads: AgentReads; onRoom: (r: Room) => void; onTest: () => void }) {
  const { ms: data, readAt, run, runNote } = reads
  const d = a.cloud?.deployment ?? null
  const list = spans(a, data)
  const ws = windows(list, data)
  const peak = ws.length ? Math.max(...ws.map((w) => w.peak)) : null
  const delays = joinDelays(a.jobs)
  const m = median(delays)
  const failed = a.jobs.filter((j) => j.failed).length
  const joinBars = a.jobs
    .filter((j) => Number.isFinite(j.dispatchedMs) && j.joinedMs !== null)
    .map((j) => ({ key: `${j.room.sid}${j.jobId}`, name: `Room ${j.room.n}${j.failed ? " · failed, retried" : ""}`, value: (j.joinedMs! - j.dispatchedMs) / 1000 }))

  return (
    <>
      <SheetHeader className="border-b px-6 py-5 pr-14">
        <div className="flex items-center justify-between gap-3">
          <SheetTitle className="text-lg">{a.name ?? "Unnamed agent"}</SheetTitle>
          <CapacityButton onClick={onTest} />
        </div>
        <SheetDescription className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
          <span className="flex items-center gap-1.5">
            {a.cloud ? <Cloud size={14} /> : <HardDrives size={14} />}
            {a.cloud ? `LiveKit Cloud, ${d?.region ?? "region unknown"}` : `Not deployed here, seen in ${a.regions.map((r) => regionCity[r] ?? r).join(", ") || "unknown region"}`}
          </span>
          {a.cloud && <span className="font-mono">{a.cloud.id}</span>}
          {d?.status && <span className="rounded-full bg-muted px-2 py-0.5 text-foreground">{d.status}</span>}
        </SheetDescription>
      </SheetHeader>

      <div className="space-y-8 px-6 py-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Peak calls at once" value={peak ?? "Unknown"} note={peak === null ? "no job in the rooms we watched" : "in the rooms we watched"} />
          <Stat label="Jobs" value={a.jobs.length} note={`${a.workers.length || "no"} ${a.workers.length === 1 ? "worker" : "workers"} seen`} />
          <Stat label="Failed" value={failed} alert={failed > 0} note={failed ? "LiveKit retried each" : undefined} />
          <Stat label="Dispatch to join" value={m === null ? "Unknown" : `${m.toFixed(1)} s`} note={m === null ? "no job with dispatch detail" : `median of ${delays.length}`} />
        </div>

        <Section title="Capacity" note="How many calls the agent carries at once before it refuses one, measured with synthetic calls.">
          {run === undefined ? (
            <p className="rounded-xl border border-dashed px-4 py-4 text-[13px] text-muted-foreground">{runNote}</p>
          ) : capacityRun(a, run) ? (
            <CapacityResult run={capacityRun(a, run)!} />
          ) : (
            <div className="flex items-center justify-between gap-4 rounded-xl border border-dashed px-4 py-4">
              <p className="text-[13px] text-muted-foreground">Not measured yet. Peak calls at once above is what traffic happened to reach, not what the agent can take.</p>
              <CapacityButton onClick={onTest} />
            </div>
          )}
        </Section>

        <Section title="Calls at once" note="Counted from the agent's own time in each room, one chart per watch. Between watches nothing was read.">
          {ws.length === 0 ? (
            <p className="rounded-xl border border-dashed px-4 py-6 text-center text-[13px] text-muted-foreground">No job in the rooms we watched.</p>
          ) : (
            ws.map(({ w, series, peak: p, open }) => (
              <div key={w.start} className="rounded-xl border p-4">
                <div className="mb-2 flex items-baseline justify-between text-[12px]">
                  <span className="font-medium">{`${day(w.start)} to ${at(w.end)}`}</span>
                  <span className="text-muted-foreground tnum">{`peak ${p}${open ? " · a room was still open when the watch ended" : ""}`}</span>
                </div>
                <ChartContainer config={config} className="h-36 w-full">
                  <AreaChart data={series} margin={{ left: -20, right: 8, top: 6 }}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="time" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={40} />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={40} domain={[0, Math.max(2, p)]} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Area dataKey="calls" type="stepAfter" stroke="var(--color-calls)" fill="var(--color-calls)" fillOpacity={0.12} isAnimationActive={false} />
                  </AreaChart>
                </ChartContainer>
              </div>
            ))
          )}
        </Section>

        <Section title="Resources" note={a.cloud ? `As LiveKit Cloud reports them. Read ${new Date(readAt).toLocaleString()}.` : undefined}>
          {a.cloud && d ? (
            <div className="grid gap-6 rounded-xl border p-4 sm:grid-cols-2">
              <Meter label="CPU" cur={cores(d.curCpu)} req={cores(d.cpuReq)} limit={cores(d.cpuLimit)} unit="cores" />
              <Meter label="Memory" cur={gib(d.curMem)} req={gib(d.memReq)} limit={gib(d.memLimit)} unit="GiB" />
              <div className="grid grid-cols-3 gap-3 text-[12px] sm:col-span-2">
                <div>
                  <p className="text-muted-foreground">Replicas</p>
                  <p className="tnum">
                    {d.replicas ?? 0} <span className="text-muted-foreground">of {d.minReplicas === d.maxReplicas ? d.maxReplicas : `${d.minReplicas} to ${d.maxReplicas}`}</span>
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Server region</p>
                  <p className="font-mono">{d.serverRegion ?? "unknown"}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Last measured</p>
                  <p className={cn(d.lastScrapedAt && Date.parse(readAt) - Date.parse(d.lastScrapedAt) > 3600e3 && "text-amber-700 dark:text-amber-400")}>{when(d.lastScrapedAt)}</p>
                </div>
              </div>
            </div>
          ) : (
            <p className="rounded-xl border border-dashed px-4 py-4 text-[13px] text-muted-foreground">Not a LiveKit Cloud deployment in this project, so no API reports its CPU, memory or replicas. Workers seen: <span className="font-mono">{a.workers.join(", ") || "not recorded"}</span>.</p>
          )}
        </Section>

        <Section title="Dispatch to join, per job" note="The caller hears nothing in this gap.">
          {joinBars.length ? (
            <div className="rounded-xl border p-4">
              <BarList data={joinBars} valueFormatter={(v) => `${v.toFixed(1)} s`} sortOrder="none" />
            </div>
          ) : (
            <p className="rounded-xl border border-dashed px-4 py-4 text-[13px] text-muted-foreground">No job with dispatch detail.</p>
          )}
        </Section>

        {a.cloud && a.cloud.versions.length > 0 && (
          <Section title="Versions">
            <ul className="divide-y rounded-xl border text-[12px]">
              {a.cloud.versions.map((v) => (
                <li key={v.version} className="flex items-center gap-2 px-4 py-2.5">
                  <span className="font-mono">{v.version}</span>
                  {v.current && <span className="rounded bg-primary/10 px-1.5 text-primary">production</span>}
                  {v.draining && <span className="rounded bg-amber-500/10 px-1.5 text-amber-700">draining</span>}
                  <span className="ml-auto text-muted-foreground">{when(v.deployedAt)}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="Jobs" note="Open a room for its participants and timeline.">
          {a.jobs.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">No jobs in the rooms we watched.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {a.jobs.map((j) => {
                const delay = Number.isFinite(j.dispatchedMs) && j.joinedMs !== null ? (j.joinedMs - j.dispatchedMs) / 1000 : null
                return (
                  <button key={`${j.room.sid}${j.jobId}`} type="button" onClick={() => onRoom(j.room)} className={cn("flex items-center gap-1 rounded-md bg-background px-1.5 py-0.5 text-[11px] ring-1 ring-border hover:bg-muted", j.failed && "text-destructive ring-destructive/40")}>
                    {j.failed && <WarningCircle size={11} />}
                    Room {j.room.n}
                    {delay !== null && <span className="text-muted-foreground tnum">· {delay.toFixed(1)} s</span>}
                  </button>
                )
              })}
            </div>
          )}
        </Section>
      </div>
    </>
  )
}

// Most calls the agent carried at once in the rooms we watched, or null when it was in none.
export function peakAtOnce(a: Agent, ms: MediaServerData): number | null {
  const ws = windows(spans(a, ms), ms)
  return ws.length ? Math.max(...ws.map((w) => w.peak)) : null
}
