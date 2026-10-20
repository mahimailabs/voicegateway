import { Bar, BarChart, Cell, XAxis, YAxis } from "recharts"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Badge } from "@/components/ui/badge"
import { type CallModel, type TurnModel, fmtMs } from "./call-model"

export function RealtimeResponse({ turn, source }: { turn: TurnModel; source: CallModel["source"] }) {
  const origin = turn.utterance?.start ?? turn.speaking?.start ?? turn.window.start
  const rows = [
    ...(turn.utterance ? [{ label: "Caller speech", interval: turn.utterance, color: "var(--muted-foreground)" }] : []),
    ...(turn.utterance && turn.speaking && turn.speaking.start >= turn.utterance.end ? [{ label: "Response gap", interval: { start: turn.utterance.end, end: turn.speaking.start }, color: "var(--chart-2)" }] : []),
    ...(turn.speaking ? [{ label: "Agent speech", interval: turn.speaking, color: "var(--primary)" }] : []),
  ].map((row) => ({ ...row, range: [(row.interval.start - origin) * 1000, (row.interval.end - origin) * 1000], duration: (row.interval.end - row.interval.start) * 1000 }))
  return <div className="space-y-4" aria-label="Realtime response timing">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">Realtime · audio-to-audio</p><p className="text-xs text-muted-foreground">No separate STT or TTS stages.</p></div><Badge variant="outline">{turn.interrupted ? "Interrupted" : "No interruption reported"}</Badge></div>
    <div className="flex items-end justify-between gap-3 rounded-xl bg-muted/50 p-4"><div><p className="text-sm">{turn.responseDerived ? "Speech-to-speech response gap" : "Reported response latency"}</p><p className="text-xs text-muted-foreground">{turn.responseDerived ? `Derived from ${source === "events" ? "session speech-state events" : "captured speech intervals"}. Not provider inference time.` : "Agent-reported timing. Provider processing is not isolated."}</p></div><strong className="text-2xl tabular-nums">{turn.responseMs === null ? "Not reported" : `${turn.responseDerived ? "~" : ""}${fmtMs(turn.responseMs)}`}</strong></div>
    {rows.length ? <ChartContainer config={{ range: { label: "Duration", color: "var(--primary)" } }} className="h-44 w-full">
      <BarChart data={rows} layout="vertical" margin={{ left: 0, right: 16, top: 8, bottom: 8 }}>
        <XAxis type="number" tickFormatter={(n: number) => `${(n / 1000).toFixed(1)} s`} tickLine={false} axisLine={false} />
        <YAxis type="category" dataKey="label" width={100} tickLine={false} axisLine={false} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideIndicator formatter={(_, __, item) => `${item.payload.label}: ${fmtMs(item.payload.duration)}`} />} />
        <Bar dataKey="range" radius={4} maxBarSize={22} isAnimationActive={false}>{rows.map((row) => <Cell key={row.label} fill={row.color} />)}</Bar>
      </BarChart>
    </ChartContainer> : <p className="text-sm text-muted-foreground">No timed speech intervals were captured for this turn.</p>}
    <p className="text-xs text-muted-foreground">Time relative to this turn’s first captured speech interval. {turn.interrupted ? "Agent speech was interrupted; its endpoint is not completion of the intended reply. " : ""}Network transit and provider execution cannot be separated from this capture.</p>
  </div>
}
