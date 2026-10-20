import { CallUsage } from "./call-usage"
import { useState } from "react"
import { ArrowLeft, CaretRight, ChatCircleText, CheckCircle, Ear, Hourglass, Info, Microphone, Sparkle, Warning, WarningCircle, Wrench, XCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Unknown } from "@/components/unknown"
import type { Call } from "@/lib/api"
import { cn } from "@/lib/utils"
import { type CallModel, type TurnModel, fmtMs } from "./call-model"
import { TtfaWaterfall } from "@/features/profile/ttfa-waterfall"
import { BUDGET_MS, type Field, type Finding, type Step, findingsFor, stepsFor, evidenceCheck } from "./call-steps"

// The right-hand panel: with no turn picked, what in this call deserves attention, ranked; with a turn
// picked, how that turn's time was spent and every step it took, with what went in and what came out.

const sevIcon: Record<Finding["severity"], { icon: Icon; tone: string }> = {
  high: { icon: WarningCircle, tone: "text-destructive" },
  medium: { icon: Warning, tone: "text-warning-foreground" },
  info: { icon: Info, tone: "text-muted-foreground" },
}

export function FindingsList({ call, model, onTurn }: { call: Call; model: CallModel; onTurn: (i: number) => void }) {
  const findings = findingsFor(call, model)
  const spans = call.spans ?? []
  const checks: { label: string; ok: boolean | null; why: string }[] = [
    { label: "Tools returned without error", ok: evidenceCheck(spans.filter((s) => s.name === "function_tool"), "lk.function_tool.is_error", (v) => v === "false"), why: "Needs an explicit error flag for every tool span" },
    { label: "No model or voice retries", ok: evidenceCheck(spans.filter((s) => s.name === "llm_request_run" || s.name === "tts_request_run"), "lk.retry_count", (v) => Number(v) === 0), why: "Needs a reported retry count for every request attempt" },
    { label: "Transcripts heard clearly", ok: evidenceCheck(spans.filter((s) => s.name === "user_turn"), "lk.transcript_confidence", (v) => Number(v) >= 0.8), why: "Needs transcript confidence on every user_turn span" },
    { label: "Every turn ended cleanly", ok: evidenceCheck(spans.filter((s) => s.name === "eou_wait"), "lk.eou.outcome", (v) => v === "committed"), why: "Needs a reported end-of-turn outcome" },
  ]

  return (
    <div className="space-y-7">
      <CallUsage call={call} />
      <section>
        <h3 className="text-[15px] font-semibold">What to look at</h3>
        {findings.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nothing in this call's data points to a problem.</p>
        ) : (
          <ol className="mt-2 divide-y">
            {findings.map((f) => {
              const { icon: I, tone } = sevIcon[f.severity]
              return (
                <li key={f.id} className="grid grid-cols-[20px_minmax(0,1fr)] gap-3 py-3.5">
                  <I size={18} weight="fill" className={cn("mt-0.5", tone)} />
                  <div className="min-w-0 space-y-1">
                    <p className="text-sm font-semibold leading-snug">{f.title}</p>
                    <p className="text-[13px] leading-relaxed text-muted-foreground">{f.detail}</p>
                    {f.turns.length > 0 && (
                      <div className="flex flex-wrap gap-x-3 gap-y-1 pt-0.5">
                        {f.turns.map((i) => (
                          <button key={i} type="button" onClick={() => onTurn(i)} className="flex items-center gap-0.5 text-[13px] font-medium text-foreground underline-offset-4 hover:underline">
                            Turn {i + 1}<CaretRight size={11} />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </section>
      <section>
        <h3 className="text-[15px] font-semibold">Checks</h3>
        <ul className="mt-2 divide-y">
          {checks.map((c) => (
            <li key={c.label} className="flex items-center gap-2.5 py-2.5 text-sm">
              {c.ok === true ? <CheckCircle size={18} weight="fill" className="text-good" /> : c.ok === false ? <XCircle size={18} weight="fill" className="text-destructive" /> : <span className="size-[18px] rounded-full border border-dashed border-muted-foreground/50" />}
              <span className={cn("flex-1", c.ok === null && "text-muted-foreground")}>{c.label}</span>
              {c.ok === null && (
                <Tooltip>
                  <TooltipTrigger asChild><span className="cursor-help text-xs text-muted-foreground underline decoration-dotted underline-offset-4">not checked</span></TooltipTrigger>
                  <TooltipContent>{c.why}</TooltipContent>
                </Tooltip>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/* ---------- One turn ---------- */

const stages = [
  { key: "eotMs", label: "End of turn", tone: "bg-foreground/25" },
  { key: "ttftMs", label: "Model first token", tone: "bg-primary" },
  { key: "ttsMs", label: "Voice first audio", tone: "bg-primary/45" },
] as const

function Anatomy({ t }: { t: TurnModel }) {
  const present = stages.filter((s) => t[s.key] !== null)
  const sum = present.reduce((a, s) => a + (t[s.key] ?? 0), 0)
  if (!present.length) return null
  return (
    <div className="space-y-2.5">
      <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        {present.map((s) => <div key={s.key} className={s.tone} style={{ width: `${((t[s.key] ?? 0) / sum) * 100}%` }} />)}
      </div>
      <div className="grid grid-cols-3 gap-3">
        {stages.map((s) => (
          <div key={s.key} className="space-y-0.5">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><span className={cn("size-2 rounded-full", t[s.key] === null ? "border border-dashed border-muted-foreground/60" : s.tone)} />{s.label}</p>
            <p className="text-[15px] font-semibold tnum">{t[s.key] === null ? <Unknown why="Not reported for this turn" /> : fmtMs(t[s.key]!)}</p>
          </div>
        ))}
      </div>
      {t.e2eMs !== null && sum > t.e2eMs * 1.05 && <p className="text-xs text-muted-foreground">The stages overlap (the model starts before the turn is confirmed), so they add up to more than the measured {fmtMs(t.e2eMs)}.</p>}
    </div>
  )
}

const kindIcon: Record<Step["kind"], Icon> = { caller: Microphone, eot: Hourglass, llm: Sparkle, tool: Wrench, speech: ChatCircleText, overlap: Ear }

function Value({ f }: { f: Field }) {
  if (f.value === null) return <p className="text-sm"><Unknown why={f.why ?? "Not reported"} /></p>
  if (f.label === "Messages sent" || f.label === "Reply") {
    const lines = f.value.split("\n").filter(Boolean)
    if (lines.every((l) => /^[a-z_]+: /.test(l))) {
      return (
        <ol className="space-y-1.5">
          {lines.map((l, k) => {
            const [role, ...rest] = l.split(": ")
            return (
              <li key={k} className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 text-[13px]">
                <span className="text-muted-foreground">{role}</span>
                <span className="break-words">{rest.join(": ")}</span>
              </li>
            )
          })}
        </ol>
      )
    }
  }
  if (f.mono) return <pre className="max-h-56 overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">{f.value}</pre>
  if (f.value.length > 140) return <p className="max-h-56 overflow-auto rounded-lg bg-muted/60 p-3 text-[13px] leading-relaxed whitespace-pre-wrap">{f.value}</p>
  return <p className="text-sm">{f.value}</p>
}

function StepRow({ s }: { s: Step }) {
  const [open, setOpen] = useState(false)
  const I = kindIcon[s.kind]
  const tone = s.status === "error" ? "text-destructive" : s.status === "warn" ? "text-warning-foreground" : "text-muted-foreground"
  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <li className="border-b last:border-b-0">
        <CollapsibleTrigger className="grid w-full grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-3 py-3 text-left">
          <I size={18} className={cn("mt-0.5", tone)} weight={s.status === "ok" || s.status === "info" ? "regular" : "fill"} />
          <span className="min-w-0">
            <span className="block text-sm font-medium">{s.title}</span>
            <span className={cn("block text-[13px] leading-snug", s.status === "error" ? "text-destructive" : s.status === "warn" ? "text-warning-foreground" : "text-muted-foreground")}>{s.summary}</span>
          </span>
          <span className="flex items-center gap-1.5 pt-0.5 text-[11px] text-muted-foreground tnum">
            {s.at !== null && `${s.at.toFixed(1)} s`}
            <CaretRight size={12} className={cn("transition-transform", open && "rotate-90")} />
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-4 pb-4 pl-8">
          {([["In", s.input], ["Out", s.output], ["Details", s.details]] as const).map(([name, fields]) =>
            fields.length ? (
              <div key={name} className="space-y-2.5">
                <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{name}</p>
                {fields.map((f) => (
                  <div key={f.label} className="space-y-1">
                    <p className="text-xs text-muted-foreground">{f.label}</p>
                    <Value f={f} />
                  </div>
                ))}
              </div>
            ) : null,
          )}
        </CollapsibleContent>
      </li>
    </Collapsible>
  )
}

export function TurnDetail({ call, model, turn, speaker = null, onBack }: { call: Call; model: CallModel; turn: number; speaker?: "caller" | "agent" | null; onBack: () => void }) {
  const t = model.turns.find((item) => item.i === turn)
  if (!t) return null
  // The caller's side is their speech and the end-of-turn wait; everything after is the agent's.
  const callerSide = (k: string) => k === "caller" || k === "eot"
  const steps = stepsFor(call, model, t).filter((x) => speaker === null || (speaker === "caller" ? callerSide(x.kind) : !callerSide(x.kind)))
  const over = t.responseMs !== null && t.responseMs > BUDGET_MS
  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" className="-ml-2 h-7 text-xs text-muted-foreground" onClick={onBack}>
        <ArrowLeft /> What to look at
      </Button>
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">Turn {t.i + 1}{speaker ? ` · ${speaker === "caller" ? "the caller" : "the agent"}` : ""}{t.userText ? ` · "${t.userText}"` : " · agent greeting"}</p>
        <p className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">
          <span className={cn("tnum", over && "text-destructive")}>{t.responseMs !== null ? fmtMs(t.responseMs) : "Unknown"}</span>{" "}
          <span className="text-muted-foreground">{t.responseMs === null ? "response time not reported" : t.responseDerived ? "to answer, derived from speech timing" : "to answer, measured end to end"}</span>
        </p>
      </div>
      {call.spans?.length ? <TtfaWaterfall call={call} model={model} turn={t.i} compact /> : <Anatomy t={t} />}
      <section>
        <h3 className="text-[15px] font-semibold">What happened</h3>
        <ol className="mt-1">{steps.map((s) => <StepRow key={s.id} s={s} />)}</ol>
      </section>
    </div>
  )
}
