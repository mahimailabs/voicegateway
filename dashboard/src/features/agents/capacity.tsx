import { CapacityPreview } from "./capacity-preview"
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bar, CartesianGrid, ComposedChart, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts"
import { type ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { CheckCircle, Gauge, Info, WarningCircle, X } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { JOIN_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import type { CapacityRun } from "@/lib/api-types"
import { type MediaServerData, openAtWatchEnd } from "../media-server/room-drawer"
import type { Agent } from "./agents-model"
import { fmtDateTime } from "@/lib/format"
import { ApiError, capacityQuery, startCapacity, stopCapacity } from "@/lib/api"
import { roleIn, useSession } from "@/lib/session"
import { currentScope } from "@/lib/url-state"

// Capacity test (decision 0022, docs/specs/capacity-runner.md): a VoiceGateway worker ramps synthetic
// calls at an idle agent until a stop rule trips, and reports the last step every call passed. The
// console starts and stops a run (Admin only) and shows each step as the worker writes it.

type Run = CapacityRun
type Step = Run["steps"][number]
// The project's capacity run (getCapacity), when it was for this agent.
export const capacityRun = (a: Agent, run: Run | null): Run | null => (run && a.name === run.agent ? run : null)
// Reported values only: an unreported join or answer time is left out, never counted as zero.
const known = (xs: (number | null)[]) => xs.filter((x): x is number => x !== null)

const STEP_SECONDS = 60
const ladder = (max: number) => Array.from({ length: Math.log2(max) + 1 }, (_, i) => 2 ** i)
const median = (xs: (number | null)[]) => {
  const k = known(xs)
  return k.length ? [...k].sort((a, b) => a - b)[Math.floor(k.length / 2)] : null
}

// Idle as far as the captured data can tell: no watched room still had this agent in it when its watch
// ended. The server checks again, live, just before it starts.
function idleNow(a: Agent, data: MediaServerData) {
  const name = a.name ?? "unnamed"
  const busy = data.rooms.filter((r) => openAtWatchEnd(r, data) && r.participants.some((p) => p.kind === "agent" && (p.agentName ?? "unnamed") === name && openAtWatchEnd(p, data)))
  // The last read: the latest known end of a watch (NaN when no watch reported one, shown as unknown).
  const ends = data.windows.flatMap((w) => (w.end === null ? [] : [w.end]))
  const lastRead = ends.length ? Math.max(...ends) : NaN
  return { idle: busy.length === 0, busy: busy.map((r) => r.name), lastRead }
}

export function CapacityButton({ onClick, label = true }: { onClick: () => void; label?: boolean }) {
  return (
    <Button
      variant="outline"
      size={label ? "sm" : "icon-sm"}
      aria-label="Test capacity"
      onClick={(e) => {
        e.stopPropagation()
        ;(e.currentTarget as HTMLElement).blur()
        onClick()
      }}
    >
      <Gauge />
      {label && "Test capacity"}
    </Button>
  )
}

const hintTone = { good: "bg-good/10 text-good", warn: "bg-amber-500/10 text-amber-800 dark:text-amber-300", bad: "bg-destructive/10 text-destructive" }
function Hint({ tone, icon: I, label, tip }: { tone: keyof typeof hintTone; icon: typeof Info; label: string; tip: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("flex cursor-help items-center gap-1 rounded-full px-2 py-0.5 text-[12px]", hintTone[tone])}>
          <I size={13} /> {label}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-72 text-xs leading-relaxed">{tip}</TooltipContent>
    </Tooltip>
  )
}

// Dev-only design preview for launch screenshots (?demo in the URL): Start runs a sample ramp in the
// browser. Never in a production build, and never written to any agent's data.
const DEMO = import.meta.env.DEV && typeof location !== "undefined" && new URLSearchParams(location.search).has("demo")
function demoRun(name: string, max: number): Run {
  const ceiling = 24
  const at = new Date().toISOString()
  const steps = ladder(max).map((n): Step => {
    const joined = Math.min(n, ceiling)
    const slow = n >= 16 ? Math.round(joined * 0.15) : 0
    return {
      step: n, startedAt: at, endedAt: at, outcome: joined === n ? "passed" : "failed",
      calls: n, joined, pending: n - joined, failed: 0, late: slow, noAnswer: 0,
      joinSeconds: Array.from({ length: joined }, (_, i) => (i < joined - slow ? 0.42 + (i % 5) * 0.09 : 2.4 + (i % 3) * 0.3)),
      answerSeconds: Array.from({ length: joined }, (_, i) => 0.92 + Math.log2(n) * 0.05 + (i % 4) * 0.04),
      testerCpuPercent: 40,
    }
  })
  const passed = steps.filter((x) => x.outcome === "passed")
  return {
    id: "demo", agent: name, maxStep: max as Run["maxStep"], state: "ended", requestedAt: at, startedAt: at, endedAt: at, stopRequested: false,
    capacity: passed.length ? passed[passed.length - 1].step : null, limitedBy: passed.length === steps.length ? "max_reached" : "pending",
    cloudAgent: false, joinThresholdMs: JOIN_MS, error: null, steps,
  }
}

// What ended a run, in a few words, and whether the number is the agent's limit or the run's.
const limits: Record<NonNullable<Run["limitedBy"]>, { label: string; agent: boolean }> = {
  pending: { label: "a job waited 10 s for the agent", agent: true },
  job_failed: { label: "a job failed", agent: true },
  answer: { label: "answers slowed by more than 50%", agent: true },
  plan: { label: "the LiveKit Cloud plan's 5 sessions", agent: true },
  max_reached: { label: "every step passed", agent: true },
  tester: { label: "limited by the tester", agent: false },
  real_call: { label: "stopped: a real call arrived", agent: false },
  agent_busy: { label: "not run: the agent was busy", agent: false },
  stopped: { label: "stopped by a person", agent: false },
  runner_lost: { label: "the worker running it stopped", agent: false },
  failed: { label: "LiveKit or storage failed", agent: false },
}
const limitText = (r: Run) => (r.limitedBy ? limits[r.limitedBy].label : r.state === "requested" ? "waiting for a worker" : "running")

// Why Start is off, or null when it is on. The server checks again, live, before the first call.
function startBlock(a: Agent, admin: boolean, idle: boolean) {
  if (!a.name) return "An agent without a name cannot be dispatched by name, so it cannot be tested."
  if (!admin) return "Only an Admin can start a capacity test."
  if (!idle) return "The agent has a call. The test runs only on an idle agent."
  return null
}

export function CapacitySetupDialog({ agent: a, ms, onOpenChange }: { agent: Agent | null; ms: MediaServerData; onOpenChange: (o: boolean) => void }) {
  const [max, setMax] = useState("32")
  const [ok, setOk] = useState(false)
  // The run this dialog watches: one it started, or one already open for the agent.
  const [watching, setWatching] = useState<string | null>(null)
  const [demoShown, setDemoShown] = useState<number | null>(null)
  const queries = useQueryClient()
  const admin = roleIn(currentScope().org, useSession()) === "Admin"
  const latest = useQuery({ ...capacityQuery(a && !DEMO ? (a.name ?? null) : null), enabled: Boolean(a?.name) && !DEMO })
  const start = useMutation({
    mutationFn: () => startCapacity(a!.name!, Number(max) as Run["maxStep"]),
    onSuccess: (run) => (setWatching(run.id), queries.setQueryData(capacityQuery(a!.name!).queryKey, run)),
  })
  const stop = useMutation({ mutationFn: (id: string) => stopCapacity(id), onSuccess: (run) => queries.setQueryData(capacityQuery(a!.name!).queryKey, run) })
  const close = (o: boolean) => {
    if (!o) {
      setOk(false)
      setWatching(null)
      setDemoShown(null)
      start.reset()
      stop.reset()
    }
    onOpenChange(o)
  }
  if (!a) return <Dialog open={false} />
  const idle = idleNow(a, ms)
  const steps = ladder(Number(max))
  const cloud = Boolean(a.cloud)
  const production = cloud && (!a.cloud?.deployment?.deployment || a.cloud.deployment.deployment === "production")
  const demo = demoRun(a.name ?? "agent", Number(max))
  const live = latest.data ?? null
  // An open run shows even when another tab started it; an ended one only when this dialog watched it.
  const run: Run | null = DEMO ? (demoShown === null ? null : { ...demo, state: demoShown < demo.steps.length ? "running" : "ended", steps: demo.steps.slice(0, demoShown) }) : live && (live.state !== "ended" || live.id === watching) ? live : null
  const phase = !run ? "setup" : run.state === "ended" ? "done" : "running"
  const block = startBlock(a, admin, idle.idle)
  const conflict = start.error instanceof ApiError && start.error.code === "run_active"
  const startDemo = () => {
    setDemoShown(0)
    demo.steps.forEach((_, i) => setTimeout(() => setDemoShown(i + 1), 900 * (i + 1)))
  }
  const ladderOf = run ? ladder(run.maxStep) : steps
  const current = run && run.steps.length ? run.steps[run.steps.length - 1] : null

  return (
    <Dialog open onOpenChange={close}>
      <DialogContent showCloseButton={false} className={cn("gap-0 overflow-hidden rounded-2xl p-0 transition-[max-width] duration-300 [&>*]:min-w-0", phase === "setup" ? "sm:max-w-[520px]" : "sm:max-w-[600px]")} onOpenAutoFocus={(e) => e.preventDefault()}>
        <div className="flex items-start gap-4 px-6 pt-6 pb-5">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl border bg-background shadow-xs">
            <Gauge size={22} />
          </span>
          <DialogHeader className="flex-1 gap-1 text-left">
            <DialogTitle className="text-[17px] font-semibold">Test capacity</DialogTitle>
            <DialogDescription className="text-[13px]">
              How many calls <span className="font-medium text-foreground">{a.name ?? "this agent"}</span> carries at once before it refuses one.
            </DialogDescription>
          </DialogHeader>
          <Button variant="ghost" size="icon-sm" className="-mt-1 -mr-2 text-muted-foreground" onClick={() => close(false)} aria-label="Close">
            <X />
          </Button>
        </div>

        {phase === "setup" ? (
          <div className="space-y-5 px-6 pb-6">
            <div className="flex flex-wrap gap-1.5">
              <Hint
                tone={idle.idle ? "good" : "bad"}
                icon={idle.idle ? CheckCircle : WarningCircle}
                label={idle.idle ? "Idle" : `Busy in ${idle.busy.length} ${idle.busy.length === 1 ? "room" : "rooms"}`}
                tip={`At the last read, ${Number.isFinite(idle.lastRead) ? fmtDateTime(idle.lastRead * 1000) : "at a time not reported"}. The server checks again before it starts, and stops if a real call arrives.`}
              />
              {production && <Hint tone="warn" icon={Info} label="Production" tip="Callers arriving during the test share this agent." />}
              {cloud && <Hint tone="warn" icon={Info} label="Per replica" tip="LiveKit Cloud adds replicas under load, so the result is per replica. The Build plan stops Cloud agents at 5 sessions; that is the plan's limit." />}
            </div>

            <div className="space-y-2">
              <p className="text-[13px] font-medium">Most calls at once</p>
              <ToggleGroup type="single" variant="outline" value={max} onValueChange={(v) => v && setMax(v)} aria-label="Most calls at once" className="grid w-full grid-cols-4">
                {["8", "16", "32", "64"].map((v) => (
                  <ToggleGroupItem key={v} value={v} className="h-10 text-[14px] font-medium tnum data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
                    {v}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>

            <div className="grid grid-cols-3 divide-x rounded-xl border bg-muted/30">
              {[
                ["Steps", `1 to ${max}`],
                ["Test calls", `up to ${max}`],
                ["Longest", `${Math.round((steps.length * STEP_SECONDS) / 60)} min`],
              ].map(([k, v]) => (
                <div key={k} className="px-4 py-3">
                  <p className="text-[12px] text-muted-foreground">{k}</p>
                  <p className="text-[17px] font-semibold tracking-[-0.01em] tnum">{v}</p>
                </div>
              ))}
            </div>
            <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
              Stops when the agent refuses a job
              <Tooltip>
                <TooltipTrigger asChild>
                  <button type="button" aria-label="All stop rules" className="hover:text-foreground">
                    <Info size={14} />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-72 text-xs leading-relaxed">{`About ${STEP_SECONDS} s per step, and calls stay up from one step to the next. Also stops when a job fails, a call gets no answer in 15 s, answers slow by more than 50%, or the tester is the limit. A join slower than ${JOIN_MS / 1000} s counts as late, not as a stop.`}</TooltipContent>
              </Tooltip>
            </p>
            {start.isError && <p role="alert" className="text-[12px] text-destructive">{conflict ? "This project already has a capacity test running. Stop it or wait for it to end." : start.error.message}</p>}
          </div>
        ) : (
          <div className="px-6 pb-6">
            {phase === "running" ? (
              <div className="space-y-3">
                <p className="flex items-center gap-2 text-[13px]" aria-live="polite">
                  <span className="relative flex size-2">
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary opacity-60" />
                    <span className="relative inline-flex size-2 rounded-full bg-primary" />
                  </span>
                  {run!.state === "requested" ? "Waiting for a worker to start the test" : current ? `Step ${ladderOf.indexOf(current.step) + 1} of ${ladderOf.length}: ${current.step} calls at once` : "Checking the agent is idle"}
                </p>
                <CapacityChart steps={run!.steps} all={ladderOf} threshold={run!.joinThresholdMs} />
              </div>
            ) : (
              <CapacityResult run={run!} bare />
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t bg-muted/30 px-6 py-4">
          {phase === "setup" ? (
            <label className="flex items-center gap-2 text-[13px]">
              <Checkbox checked={ok} onCheckedChange={(v) => setOk(v === true)} />
              Place real calls through my providers
            </label>
          ) : (
            <span className="text-[12px] text-muted-foreground tnum">{phase === "done" ? `${run!.steps.length} of ${ladderOf.length} steps run` : run!.stopRequested ? "Stopping" : "Rooms vg-capacity-*"}</span>
          )}
          <div className="flex gap-2">
            {phase === "setup" ? (
              <>
                <Button variant="ghost" onClick={() => close(false)}>
                  Cancel
                </Button>
                {block && !DEMO ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span tabIndex={0}>
                        <Button disabled>
                          <Gauge /> Start test
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-64 text-xs">{block}</TooltipContent>
                  </Tooltip>
                ) : (
                  <Button disabled={!ok || start.isPending || (DEMO && !idle.idle)} onClick={() => (DEMO ? startDemo() : start.mutate())}>
                    <Gauge /> Start test
                  </Button>
                )}
              </>
            ) : phase === "running" && !DEMO ? (
              <Button variant="outline" disabled={!admin || run!.stopRequested || stop.isPending} onClick={() => stop.mutate(run!.id)}>
                Stop test
              </Button>
            ) : (
              <Button variant={phase === "done" ? "default" : "outline"} onClick={() => close(false)}>
                {phase === "done" ? "Done" : "Close"}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// One column per step: calls that got the agent in time, late (slower than the join threshold, a new
// process had to start), or refused or failed, with the median answer time as a line on its own
// axis. Steps not run yet keep their slot.
const chartConfig = {
  inTime: { label: "Joined in time", color: "var(--chart-1)" },
  late: { label: "Joined late", color: "oklch(0.77 0.16 70)" },
  refused: { label: "Refused or failed", color: "var(--destructive)" },
  answer: { label: "Answer time (s)", color: "var(--foreground)" },
} satisfies ChartConfig

// A step's late joins: the runner's count, or, for a run that did not report one, the reported join
// times over the threshold (a floor when any join time is unreported).
const lateOf = (s: Step, thresholdMs: number) => s.late ?? known(s.joinSeconds).filter((j) => j * 1000 > thresholdMs).length
const lateIsFloor = (s: Step) => s.late === null && s.joinSeconds.some((j) => j === null)

function CapacityChart({ steps, all, ceiling, threshold }: { steps: Step[]; all: number[]; ceiling?: number | null; threshold: number | null }) {
  const ms = threshold ?? JOIN_MS
  const rows = all.map((n) => {
    const s = steps.find((x) => x.step === n)
    if (!s) return { step: String(n) }
    const late = lateOf(s, ms)
    // The in-time bar needs the joined count and an exact late count; otherwise it is left out.
    const inTime = s.joined === null || lateIsFloor(s) ? null : s.joined - late
    const refused = s.pending === null ? null : s.pending + (s.failed ?? 0)
    return { step: String(n), inTime, late, refused, answer: median(s.answerSeconds) }
  })
  const top = Math.max(1, ...all)
  return (
    <ChartContainer config={chartConfig} className="h-56 w-full">
      <ComposedChart data={rows} margin={{ top: 12, right: 4, left: -18, bottom: 0 }} barCategoryGap="22%">
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="step" tickLine={false} axisLine={false} tickMargin={8} tickFormatter={(v) => `${v}`} />
        <YAxis yAxisId="calls" tickLine={false} axisLine={false} allowDecimals={false} domain={[0, top]} width={40} />
        <YAxis yAxisId="answer" orientation="right" tickLine={false} axisLine={false} width={36} domain={[0, (max: number) => Math.max(2, Math.ceil(max + 0.5))]} tickFormatter={(v) => `${v}s`} />
        <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent labelFormatter={(l) => `${l} calls at once`} />} />
        <Bar yAxisId="calls" dataKey="inTime" stackId="a" fill="var(--color-inTime)" radius={[0, 0, 4, 4]} isAnimationActive={false} />
        <Bar yAxisId="calls" dataKey="late" stackId="a" fill="var(--color-late)" isAnimationActive={false} />
        <Bar yAxisId="calls" dataKey="refused" stackId="a" fill="var(--color-refused)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
        <Line yAxisId="answer" dataKey="answer" type="monotone" stroke="var(--color-answer)" strokeWidth={1.5} dot={{ r: 2.5, fill: "var(--background)", strokeWidth: 1.5 }} isAnimationActive={false} connectNulls />
        {ceiling ? <ReferenceLine yAxisId="calls" y={ceiling} stroke="var(--foreground)" strokeDasharray="4 4" label={{ value: `Capacity ${ceiling}`, position: "insideTopLeft", fontSize: 11, fill: "var(--foreground)" }} /> : null}
        <ChartLegend content={<ChartLegendContent />} />
      </ComposedChart>
    </ChartContainer>
  )
}

function answerTrend(xs: number[]) {
  if (xs.length < 2) return "one step only"
  const d = Math.round((xs[xs.length - 1] / xs[0] - 1) * 100)
  return Math.abs(d) < 5 ? "steady under load" : d > 0 ? `${d}% slower at the top` : `${-d}% faster at the top`
}

function Tile({ label, foot, warn, children }: { label: string; foot: string; warn?: boolean; children: React.ReactNode }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <div className="mt-1.5 flex items-baseline">{children}</div>
      <p className={cn("mt-1.5 truncate text-[11px]", warn ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>{foot}</p>
    </div>
  )
}

// The result of a run: the numbers first, then each step.
export function CapacityResult({ run: r, bare }: { run: Run; bare?: boolean }) {
  const value = r.capacity
  const ms = r.joinThresholdMs ?? JOIN_MS
  const answers = r.steps.map((x) => median(x.answerSeconds)).filter((v): v is number => v !== null)
  // Calls stay up from one step to the next, so the most that joined is the last step's count.
  const last = r.steps.length ? r.steps[r.steps.length - 1] : null
  const joined = last ? last.joined : null
  const late = last ? lateOf(last, ms) : null
  const lateFloor = last ? lateIsFloor(last) : false
  const limit = r.limitedBy ? limits[r.limitedBy] : null
  return (
    <div className={cn("space-y-4", !bare && "rounded-xl border p-4")}>
      <div className="grid grid-cols-1 divide-y rounded-xl border bg-muted/30 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <Tile label="Capacity" foot={limitText(r)} warn={limit !== null && !limit.agent}>
          <span className="text-[28px] leading-none font-semibold tracking-[-0.03em] tnum">{value ?? "Unknown"}</span>
          <span className="ml-1.5 text-[12px] text-muted-foreground">at once</span>
        </Tile>
        <Tile label="Median answer" foot={answerTrend(answers)}>
          <span className="flex items-end justify-between gap-2">
            <span className="text-[20px] leading-none font-semibold tracking-[-0.02em] tnum">{answers.length ? `${answers[0].toFixed(1)}→${answers[answers.length - 1].toFixed(1)} s` : "Unknown"}</span>
            {answers.length > 1 && (
              <ChartContainer config={chartConfig} className="aspect-auto h-6 w-14">
                <LineChart data={answers.map((v, i) => ({ i, answer: v }))} margin={{ top: 2, bottom: 2, left: 2, right: 2 }}>
                  <YAxis hide domain={["dataMin", "dataMax"]} />
                  <Line dataKey="answer" type="monotone" stroke="var(--color-inTime)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                </LineChart>
              </ChartContainer>
            )}
          </span>
        </Tile>
        <Tile label="Late joins" foot={`of ${joined ?? "an unknown number"}, slower than ${ms / 1000} s`}>
          <span className="text-[28px] leading-none font-semibold tracking-[-0.03em] tnum">{late === null ? "Unknown" : lateFloor ? `≥${late}` : late}</span>
        </Tile>
      </div>
      <CapacityChart steps={r.steps} all={ladder(r.maxStep)} ceiling={value} threshold={r.joinThresholdMs} />
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {`${r.endedAt ? `Ran ${fmtDateTime(Date.parse(r.endedAt))}` : "Running"}${r.cloudAgent ? ", per LiveKit Cloud replica" : ""}.${r.error ? ` Error: ${r.error}.` : ""}`}
      </p>
    </div>
  )
}

// Local UI preview is isolated from the real run and never submits a test.
export function CapacityDialog(props: { agent: Agent | null; ms: MediaServerData; onOpenChange: (o: boolean) => void }) {
  if (import.meta.env.DEV) return props.agent ? <CapacityPreview key={props.agent.key} name={props.agent.name ?? "Unnamed agent"} onClose={() => props.onOpenChange(false)} /> : <Dialog open={false} />
  return <CapacitySetupDialog {...props} />
}
