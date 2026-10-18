import { Gauge, GaugeHub, GaugeNeedle, GaugeTickLabels, GaugeTicks, GaugeZones } from "@/components/gauge"
import { motion } from "motion/react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { StageTiming } from "@/lib/api-types"
import { cn } from "@/lib/utils"
import { fmtMs } from "@/features/call/call-model"

// The Overview's own visuals. Each draws measured values only; a stage nobody reports is drawn as a
// hatched block that says so, never as a short bar.

const fmtS = (ms: number) => `${(ms / 1000).toFixed(2)} s`

// Median answer on a gauge-ui dial: green under two thirds of the budget, amber up to it, red past it.
// The needle sits on the measured median; the value is printed under the dial, outside the SVG.
export function AnswerGauge({ p50, budget, max = 5000 }: { p50: number | null; budget: number; max?: number }) {
  return (
    <Gauge value={p50 === null ? 0 : Math.min(p50, max)} min={0} max={max} startAngle={90} endAngle={270} padding={30} fit="content" className="w-full max-w-72">
      <GaugeZones
        width={14}
        gap={2}
        zones={[
          { to: budget * 0.67, color: "var(--good)" },
          { to: budget, color: "oklch(0.78 0.15 75)" },
          { to: max, color: "var(--destructive)" },
        ]}
      />
      <GaugeTicks count={10} length={6} width={1.5} offset={-16} opacity={0.35} />
      <GaugeTickLabels count={5} fontSize={15} offset={-38} color="var(--muted-foreground)" format={(v: number) => `${Math.round(v / 1000)}s`} />
      {p50 !== null && <GaugeNeedle length={0.66} width={5} color="var(--foreground)" />}
      <GaugeHub radius={9} color="var(--foreground)" />
    </Gauge>
  )
}

// Every turn as a dot on one 0 to 5 s line, stacked where they collide, red past the budget.
export type TurnDot = { ms: number; room: string; turn: number | null; agent: string }
export function TurnStrip({ dots, budget, max = 5000 }: { dots: TurnDot[]; budget: number; max?: number }) {
  const bin = 120
  const stacks = new Map<number, number>()
  const placed = [...dots]
    .sort((a, b) => a.ms - b.ms)
    .map((d) => {
      const b = Math.floor(Math.min(d.ms, max) / bin)
      const level = stacks.get(b) ?? 0
      stacks.set(b, level + 1)
      return { ...d, level }
    })
  const top = Math.max(1, ...placed.map((p) => p.level + 1))
  const x = (ms: number) => `${(Math.min(ms, max) / max) * 100}%`
  return (
    <div className="space-y-2">
      <div className="relative" style={{ height: Math.max(64, top * 12 + 12) }}>
        <div className="absolute inset-y-0 right-0 rounded-r-lg bg-destructive/[0.06]" style={{ left: x(budget) }} />
        <div className="absolute inset-y-0 border-l border-dashed border-foreground/40" style={{ left: x(budget) }}>
          <span className="absolute -top-0.5 left-1.5 text-[10px] text-muted-foreground">{`${budget / 1000} s budget`}</span>
        </div>
        <div className="absolute inset-x-0 bottom-0 h-px bg-border" />
        {placed.map((d, k) => (
          <Tooltip key={k}>
            <TooltipTrigger asChild>
              <motion.span
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: k * 0.015, duration: 0.3 }}
                className={cn("absolute size-2.5 -translate-x-1/2 rounded-full ring-2 ring-background", d.ms > budget ? "bg-destructive" : "bg-primary")}
                style={{ left: x(d.ms), bottom: 4 + d.level * 12 }}
              />
            </TooltipTrigger>
            <TooltipContent className="text-xs">{`${fmtMs(d.ms)} · ${d.turn === null ? "turn unknown" : `turn ${d.turn + 1}`} · ${d.room} · ${d.agent}`}</TooltipContent>
          </Tooltip>
        ))}
      </div>
      <div className="relative h-3 text-[10px] text-muted-foreground tnum">
        {[0, 1, 2, 3, 4, 5].map((v) => (
          <span key={v} className="absolute -translate-x-1/2" style={{ left: x(v * 1000) }}>{`${v}s`}</span>
        ))}
      </div>
    </div>
  )
}

// The anatomy of a turn, DevTools style: one lane per stage on one clock from the moment the caller
// stops talking. The solid bar runs from the stage's median start to its median end; the pale tail
// runs on to its 90th percentile end. Stages overlap where the agent works in parallel. Stages nobody
// measures keep their lane and say "not reported".
export type Lane = { key: string; label: string; field: string; start: number; end: number; p90: number; n: number | null; tone: string; missing?: "before" | "after" }
export const stageDefs: { key: string; label: string; field: string; tone: string }[] = [
  { key: "Speech to text", label: "Final transcript", field: "transcription_delay", tone: "bg-muted-foreground/45" },
  { key: "Turn detection", label: "Turn detector", field: "eou_detection span", tone: "bg-muted-foreground/60" },
  { key: "Turn confirmed", label: "Endpointing wait", field: "end_of_turn_delay", tone: "bg-muted-foreground/30" },
  { key: "LLM, call 1", label: "Model first token", field: "llm_request ttft", tone: "bg-primary" },
  { key: "Tool", label: "Tools", field: "function_tool span", tone: "bg-foreground/75" },
  { key: "LLM, call 2", label: "Model after tools", field: "second llm_request ttft", tone: "bg-primary/75" },
  { key: "Text to speech", label: "Voice first audio", field: "tts_node ttfb", tone: "bg-primary/50" },
  { key: "To the caller", label: "Agent to media server", field: "network probe, round trip", tone: "bg-primary/25" },
]
const before = [
  { key: "net", label: "Caller's network", field: "WebRTC stats, caller side only" },
  { key: "phone", label: "Phone leg", field: "carrier and PSTN, setup only" },
  { key: "nc", label: "Noise cancellation", field: "Krisp, no timing exposed" },
  { key: "vad", label: "Voice activity detection", field: "VADMetrics has inference time only" },
]
// Turn anatomy's lanes from the stage timings getCallStats returns. The realtime lane names the agents
// whose calls reported it.
export function lanesFrom(stages: StageTiming[], realtimeAgents: string[]): Lane[] {
  const lanes: Lane[] = []
  for (const st of stages) {
    if (st.startMs === null || st.medianMs === null || st.p90Ms === null) continue
    const def = stageDefs.find((d) => d.key === st.stage)
    if (def) lanes.push({ key: def.key, label: def.label, field: def.field, start: st.startMs, end: st.medianMs, p90: st.p90Ms, n: st.turns, tone: def.tone })
    else if (st.stage === "Realtime") {
      const who = realtimeAgents.join(", ")
      lanes.push({ key: "rt", label: `Realtime · ${who}`, field: `${st.field} · used by ${who} only`, start: st.startMs, end: st.medianMs, p90: st.p90Ms, n: st.turns, tone: "bg-violet-500/70" })
    }
  }
  return lanes
}

export function TurnAnatomy({ lanes, e2e, turns, budget }: { lanes: Lane[]; e2e: number | null; turns: number | null; budget: number }) {
  const end = Math.max(e2e ?? 0, budget, ...lanes.map((l) => l.p90)) * 1.08
  const pre = end * 0.13
  const span = end + pre
  const x = (ms: number) => ((ms + pre) / span) * 100
  const ticks = Array.from({ length: Math.floor(end / 500) + 1 }, (_, i) => i * 500)
  const rows: Lane[] = [
    ...before.map((b) => ({ ...b, start: 0, end: 0, p90: 0, n: 0, tone: "", missing: "before" as const })),
    ...lanes,
    { key: "back", label: "Media server to caller", field: "caller side only", start: 0, end: 0, p90: 0, n: 0, tone: "", missing: "after" as const },
  ]
  const H = 26
  return (
    // Phones scroll the chart sideways inside its card rather than squeeze the lanes.
    <div className="-mx-1 overflow-x-auto px-1">
      <div className="flex min-w-[600px] gap-4">
        <div className="w-44 shrink-0 pt-6 sm:w-56">
          {rows.map((l) => (
            <Tooltip key={l.key}>
              <TooltipTrigger asChild>
                <div className={cn("flex cursor-help items-center justify-end truncate text-right text-[12px]", l.missing && "text-muted-foreground")} style={{ height: H }}>
                  {l.label}
                </div>
              </TooltipTrigger>
              <TooltipContent className="text-xs">{l.missing ? `${l.field}: not reported` : `${l.field} · ${l.n ?? "an unknown number"} of ${turns ?? "an unknown number of"} turns`}</TooltipContent>
            </Tooltip>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <div className="relative h-6 text-[11px]">
            <span className="absolute -translate-x-1/2 font-medium whitespace-nowrap" style={{ left: `${x(0)}%` }}>
              Caller stops
            </span>
            {e2e !== null && (
              <span className={cn("absolute font-medium whitespace-nowrap", x(e2e) > 75 ? "-translate-x-full pr-1" : "-translate-x-1/2")} style={{ left: `${x(e2e)}%` }}>
                {`Agent speaks · ${fmtS(e2e)}`}
              </span>
            )}
          </div>
          <div className="relative rounded-lg bg-muted/30" style={{ height: rows.length * H }}>
            {ticks.map((t) => (
              <div key={t} className="absolute inset-y-0 border-l border-border/70" style={{ left: `${x(t)}%` }} />
            ))}
            <div className="absolute inset-y-0 border-l-2 border-dashed border-destructive/50" style={{ left: `${x(budget)}%` }} />
            <div className="absolute inset-y-0 border-l-2 border-foreground" style={{ left: `${x(0)}%` }} />
            {e2e !== null && <div className="absolute inset-y-0 border-l-2 border-foreground" style={{ left: `${x(e2e)}%` }} />}
            {rows.map((l, k) => (
              <div key={l.key} className={cn("absolute inset-x-0", k % 2 === 1 && "bg-background/40")} style={{ top: k * H, height: H }}>
                {l.missing ? (
                  <span
                    className="absolute top-[6px] flex h-[14px] items-center justify-center rounded-[4px] border border-dashed border-muted-foreground/40 bg-[repeating-linear-gradient(135deg,transparent,transparent_4px,var(--muted)_4px,var(--muted)_8px)] text-[9px] text-muted-foreground"
                    style={l.missing === "before" ? { left: "1%", width: `${x(0) - 2}%` } : { left: `${x(e2e ?? 0) + 0.5}%`, width: `${Math.max(100 - x(e2e ?? 0) - 1.5, 6)}%` }}
                  >
                    not reported
                  </span>
                ) : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="absolute inset-y-0 cursor-help" style={{ left: `${x(l.start)}%`, width: `${Math.max(x(l.p90) - x(l.start), 0.6)}%` }}>
                        <motion.span initial={{ scaleX: 0, opacity: 0 }} animate={{ scaleX: 1, opacity: 1 }} transition={{ delay: 0.05 + k * 0.05, duration: 0.5, ease: [0.22, 1, 0.36, 1] }} className="absolute inset-0 origin-left">
                          <span className={cn("absolute top-[6px] h-[14px] rounded-[4px] opacity-35", l.tone)} style={{ left: 0, right: 0 }} />
                          <span className={cn("absolute top-[6px] h-[14px] rounded-[4px]", l.tone)} style={{ left: 0, width: `${((l.end - l.start) / Math.max(l.p90 - l.start, 1)) * 100}%` }} />
                        </motion.span>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="text-xs">{`${l.label}: ${fmtMs(l.end - l.start)} median, ${fmtMs(l.p90 - l.start)} at p90 · ${l.field} · ${l.n ?? "an unknown number"} of ${turns ?? "an unknown number of"} turns`}</TooltipContent>
                  </Tooltip>
                )}
                {!l.missing && (
                  <span className="pointer-events-none absolute top-[5px] pl-1.5 text-[11px] leading-4 font-medium tnum" style={{ left: `${x(l.p90)}%` }}>
                    {fmtMs(l.end - l.start)}
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className="relative mt-1.5 h-7 text-[10px] text-muted-foreground tnum">
            {ticks.map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: `${x(t)}%` }}>{`${(t / 1000).toFixed(1)}s`}</span>
            ))}
            <span className="absolute top-3.5 -translate-x-1/2 text-destructive" style={{ left: `${x(budget)}%` }}>
              budget
            </span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-5 rounded-sm bg-primary" />
              median
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-5 rounded-sm bg-primary opacity-35" />
              to p90
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-5 rounded-sm border border-dashed border-muted-foreground/50" />
              not reported
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

// Each call as a block, coloured by its median answer against the budget.
// One block per call, coloured by its median answer. With onOpen, each block is a button that opens
// its call.
export function CallTracker({ calls, budget, onOpen }: { calls: { id?: string; room: string; p50: number | null }[]; budget: number; onOpen?: (id: string) => void }) {
  const tone = (p50: number | null) => (p50 === null ? "bg-muted-foreground/20" : p50 > budget ? "bg-destructive" : p50 > budget * 0.67 ? "bg-amber-500" : "bg-good")
  return (
    <div className="flex h-7 gap-[3px]">
      {calls.map((c) => (
        <Tooltip key={c.id ?? c.room}>
          <TooltipTrigger asChild>
            {onOpen && c.id ? (
              <button type="button" aria-label={`Open the call in ${c.room}`} onClick={() => onOpen(c.id!)} className={cn("h-full min-w-1.5 flex-1 cursor-pointer rounded-[3px] transition-opacity hover:opacity-70", tone(c.p50))} />
            ) : (
              <span className={cn("h-full min-w-1.5 flex-1 rounded-[3px] transition-opacity hover:opacity-70", tone(c.p50))} />
            )}
          </TooltipTrigger>
          <TooltipContent className="text-xs">{`${c.room} · ${c.p50 === null ? "answer not measured" : fmtMs(c.p50)}`}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  )
}
