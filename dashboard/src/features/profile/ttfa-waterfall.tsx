import { turnSpans, userTurnFor, numericAttr } from "@/features/call/call-trace"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Call } from "@/lib/api"
import { cn } from "@/lib/utils"
import { type CallModel, type Placed, type TurnModel, fmtMs, placed } from "@/features/call/call-model"

// Only durations and positions captured on the turn's own trace clock belong in this chart.
// Independent connectivity probes cannot split a request into network and provider time.
type Row = { label: string; sub: string; start: number; end: number; kind: "model" | "turn" | "tool" }
const sec = (s: Placed) => s.startMs / 1000

export function waterfallRows(call: Call, t: TurnModel): Row[] | null {
  const { root, children } = turnSpans(call, t.i)
  if (!root || !placed(root)) return null
  const user = userTurnFor(call, root)
  const speech = user.children.filter(placed).filter((s) => s.name === "user_speaking")
  const speechEnd = speech.length ? Math.max(...speech.map((s) => sec(s) + s.durationMs / 1000)) : null
  const t0 = speechEnd ?? sec(root)
  const rows: Row[] = []
  const add = (span: Placed, label: string, sub: string, duration: number, kind: Row["kind"]) => {
    const start = (sec(span) - t0) * 1000
    if (duration >= 0 && Number.isFinite(duration)) rows.push({ label, sub, start, end: start + duration, kind })
  }
  const td = call.turns?.[t.i]?.transcriptionDelayMs
  if (speechEnd !== null && td != null) rows.push({ label: "STT delay", sub: "reported transcription delay", start: 0, end: td, kind: "model" })
  const det = user.children.filter(placed).filter((s) => s.name === "eou_detection")
  for (const d of det) add(d, "Turn detection", "detector execution", d.durationMs, "turn")
  for (const n of children.filter(placed)) {
    if (n.name === "llm_node") {
      const ttft = numericAttr(n, "lk.response.ttft", -1)
      if (ttft !== null) add(n, "LLM first token", n.attrs?.["gen_ai.request.model"] ?? "reported TTFT", ttft * 1000, "model")
    }
    if (n.name === "tts_node") {
      const ttfb = numericAttr(n, "lk.response.ttfb", -1)
      if (ttfb !== null) add(n, "TTS first audio", n.attrs?.["gen_ai.request.model"] ?? "reported TTFB", ttfb * 1000, "model")
    }
    if (n.name === "function_tool") add(n, "Tool", n.attrs?.["lk.function_tool.name"] ?? "tool execution", n.durationMs, "tool")
  }
  return rows.length ? rows : null
}

const tone: Record<Row["kind"], string> = {
  model: "bg-primary",
  turn: "bg-foreground/35",
  tool: "bg-foreground/70",
}

export function TtfaWaterfall({ call, model, turn, compact }: { call: Call; model: CallModel; turn: number; compact?: boolean }) {
  const t = model.turns.find((item) => item.i === turn)
  const rows = t ? waterfallRows(call, t) : null
  if (!t || !rows) return <p className="text-sm text-muted-foreground">This turn has no reported stage timings to draw. Inspect its trace for available evidence.</p>
  const min = Math.min(0, ...rows.map((r) => r.start))
  const max = Math.max(1, ...rows.map((r) => r.end))
  const range = max - min
  const ticks = Array.from({ length: 6 }, (_, k) => min + range * k / 5)
  const pct = (ms: number) => `${((ms - min) / range) * 85}%`
  const width = (ms: number) => `${(ms / range) * 85}%`
  const user = userTurnFor(call, turnSpans(call, t.i).root)
  const origin = user.children.some((s) => s.name === "user_speaking" && placed(s)) ? "caller speech ended" : "agent turn started"
  return (
    <div className="space-y-4">
      <p className="text-[12px] text-muted-foreground">Trace clock, relative to when {origin}. Stages may overlap; their sum is not end-to-end latency.</p>
      <div className="space-y-2">
        {rows.map((r, k) => (
          <div key={k} className={cn("grid items-center gap-4", compact ? "grid-cols-[120px_minmax(0,1fr)]" : "grid-cols-[150px_minmax(0,1fr)]")}>
            <div className="min-w-0 text-right">
              <p className="truncate text-[13px] font-medium">{r.label}</p>
              <p className="truncate text-[11px] text-muted-foreground">{r.sub}</p>
            </div>
            <div className="relative h-7">
              <div className="absolute inset-y-0 left-0 right-0 border-l border-border" />
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className={cn("absolute inset-y-1 flex items-center overflow-hidden rounded-md", tone[r.kind])} style={{ left: pct(r.start), width: `max(${width(r.end - r.start)}, 6px)` }}>
                  </div>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  {`${r.label}: ${fmtMs(r.end - r.start)}, starting ${Math.round(r.start)} ms relative to when ${origin}.`}
                </TooltipContent>
              </Tooltip>
              <span className="absolute top-1/2 -translate-y-1/2 pl-1.5 text-[11px] font-medium whitespace-nowrap tnum" style={{ left: pct(r.end) }}>
                {fmtMs(r.end - r.start)}
              </span>
            </div>
          </div>
        ))}
        <div className={cn("grid gap-4 pt-1 text-[11px] text-muted-foreground tnum", compact ? "grid-cols-[120px_minmax(0,1fr)]" : "grid-cols-[150px_minmax(0,1fr)]")}>
          <span />
          <div className="relative h-3">
            {ticks.map((v, k) => (
              <span key={k} className={cn("absolute -translate-x-1/2", k === 0 && "translate-x-0")} style={{ left: pct(v) }}>{Math.round(v)} ms</span>
            ))}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4 rounded-xl bg-muted/60 px-4 py-3">
        <div>
          <p className="text-[13px] font-medium">Time to first audio</p>
          <p className="text-xs text-muted-foreground tnum">
            Reported by the agent pipeline. Carrier and handset delay are not measured here.
          </p>
        </div>
        <p className="text-[28px] leading-none font-semibold tracking-[-0.02em] tnum">{t.e2eMs !== null ? fmtMs(t.e2eMs) : t.responseMs !== null ? `~${fmtMs(t.responseMs)}` : "Unknown"}</p>
      </div>
    </div>
  )
}
