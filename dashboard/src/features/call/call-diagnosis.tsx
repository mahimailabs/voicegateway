import { RealtimeResponse } from "./realtime-response"
import { ChatCircleText, Clock, PhoneDisconnect, ArrowRight } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useUrlState } from "@/lib/url-state"
import { BUDGET_MS } from "@/lib/thresholds"
import type { Call } from "@/lib/api"
import { type CallModel, fmtMs } from "./call-model"
import { TtfaWaterfall } from "../profile/ttfa-waterfall"
import { stepsFor } from "./call-steps"

export function CallDiagnosis({ call, model, onConversation }: { call: Call; model: CallModel; onConversation: (turn: number) => void }) {
  const slowest = model.turns.reduce<typeof model.turns[number] | undefined>((best, t) => t.responseMs !== null && (best?.responseMs == null || t.responseMs > best.responseMs) ? t : best, undefined)
  const [selected, setSelected] = useUrlState("diagnosisTurn", (slowest?.i ?? model.turns[0]?.i ?? 0) + 1)
  const turn = model.turns.find((t) => t.i === Number(selected) - 1) ?? slowest ?? model.turns[0]
  const index = turn ? model.turns.indexOf(turn) : 0
  const pageStart = Math.floor(index / 6) * 6
  const visibleTurns = model.turns.slice(pageStart, pageStart + 6)
  const scale = Math.max(BUDGET_MS, ...model.turns.map((t) => t.responseMs ?? 0))
  const steps = turn ? stepsFor(call, model, turn) : []
  return <section aria-label="Turn diagnosis" className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 pt-6 pb-16 sm:px-8 lg:px-10 scroll-pb-16">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-xl font-semibold">Response latency by turn</h2><Badge variant="outline"><Clock />Response budget {fmtMs(BUDGET_MS)}</Badge></div>
    {turn && <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2"><Button size="sm" variant="outline" disabled={index === 0} onClick={() => setSelected(model.turns[index - 1].i + 1)}>Previous turn</Button><span className="text-xs tabular-nums">{index + 1} / {model.turns.length}</span><Button size="sm" variant="outline" disabled={index === model.turns.length - 1} onClick={() => setSelected(model.turns[index + 1].i + 1)}>Next turn</Button></div>

    </div>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="Select a turn">
      {visibleTurns.map((t) => <Button key={t.i} variant={t.i === turn?.i ? "secondary" : "outline"} aria-pressed={t.i === turn?.i} onClick={() => setSelected(t.i + 1)} className="h-auto min-w-0 whitespace-normal flex-col items-stretch gap-2 p-3">
        <span className="flex flex-wrap items-center justify-between gap-2"><span>Turn {t.i + 1}</span>{" "}<span className="tabular-nums text-xs">{t.responseMs === null ? "Not measured" : `${t.responseDerived ? "~" : ""}${fmtMs(t.responseMs)}`}</span></span>
        {t.responseMs !== null && <Progress aria-label={`Turn ${t.i + 1} response relative to longest response or budget`} value={t.responseMs / scale * 100} className="h-1.5" />}
        <span className="text-left text-xs font-normal text-muted-foreground">{t.responseMs === null ? "Timing unavailable" : t.responseMs > BUDGET_MS ? "Over response budget" : "Within response budget"}{t.i === slowest?.i ? " · Slowest" : ""}</span>
      </Button>)}
    </div>
    {model.turns.length > 6 && <div className="flex items-center justify-between text-xs text-muted-foreground"><Button size="sm" variant="ghost" disabled={pageStart === 0} onClick={() => setSelected(model.turns[Math.max(0, pageStart - 6)].i + 1)}>Previous 6</Button><span>Turns {pageStart + 1}–{Math.min(pageStart + 6, model.turns.length)}</span><Button size="sm" variant="ghost" disabled={pageStart + 6 >= model.turns.length} onClick={() => setSelected(model.turns[pageStart + 6].i + 1)}>Next 6</Button></div>}
    {turn ? <Card className="shadow-none">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3"><CardTitle>Turn {turn.i + 1} · Response breakdown</CardTitle><Button variant="ghost" size="sm" onClick={() => onConversation(turn.i)}><ChatCircleText />Open transcript<ArrowRight /></Button></CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 border-b pb-4 sm:grid-cols-2"><div><p className="mb-1 text-xs text-muted-foreground">Caller</p><p className="text-sm">{turn.userText || "No caller transcript captured"}</p></div><div><p className="mb-1 text-xs text-muted-foreground">Agent</p><p className="text-sm">{turn.agentText || "No agent transcript captured"}</p></div></div>
        {model.pipeline === "realtime" ? <RealtimeResponse turn={turn} source={model.source} /> : <>
        <div className="grid grid-cols-3 gap-3" aria-label="Reported turn metrics">{[{ label: "End-of-turn delay", value: turn.eotMs }, { label: "LLM TTFT", value: turn.ttftMs }, { label: "TTS TTFB", value: turn.ttsMs }].map((metric) => <div key={metric.label}><p className="text-xs text-muted-foreground">{metric.label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{metric.value === null ? "Not reported" : fmtMs(metric.value)}</p></div>)}</div>
        <p className="text-xs text-muted-foreground">Reported durations are not additive. The timeline below includes only stages we can position on the turn’s trace clock.</p>
        <TtfaWaterfall call={call} model={model} turn={turn.i} compact />
        </>}
        <details className="border-t pt-3"><summary className="cursor-pointer text-sm font-medium">Inspect source evidence</summary><div className="mt-3 divide-y">{steps.map((step) => <details key={step.id} className="py-3"><summary className="cursor-pointer text-sm">{step.title}</summary><p className="mt-2 text-xs text-muted-foreground">{step.summary}</p><dl className="mt-2 space-y-2">{[...step.input, ...step.output, ...step.details].filter((field) => field.value !== null).map((field, i) => <div key={i} className="text-xs"><dt className="text-muted-foreground">{field.label}</dt><dd className="mt-0.5 whitespace-pre-wrap break-words">{field.value}</dd></div>)}</dl></details>)}</div></details>
      </CardContent>
    </Card> : <p className="text-sm text-muted-foreground">No turn timing evidence was captured for this call.</p>}
    <div className="flex gap-3 rounded-xl border p-4"><PhoneDisconnect size={22} className="mt-0.5 shrink-0 text-muted-foreground" /><div className="min-w-0 space-y-2"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">Call termination</h3><Badge variant="outline">Closure: {call.endState}</Badge>{call.endedAt && <time dateTime={call.endedAt} className="text-xs text-muted-foreground">{new Date(call.endedAt).toLocaleTimeString()}</time>}</div><p className="text-sm">{call.endReason || "No termination reason was captured."}</p><p className="text-xs text-muted-foreground">Call-level evidence. This does not establish that response latency caused the disconnect.</p></div></div>
  </section>
}
