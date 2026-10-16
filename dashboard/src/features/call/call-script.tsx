import { ArrowBendDownRight, ChatTeardropDots, Robot, Scissors, User } from "@phosphor-icons/react"
import { useAudioPlayer, useAudioPlayerTime } from "@/components/elevenlabs/audio-player"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Tool } from "@/components/ui/tool"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Call, Span } from "@/lib/api"
import { cn } from "@/lib/utils"
import { type CallModel, fmtMs } from "./call-model"
import { BUDGET_MS } from "./call-steps"
import { clock } from "./call-timeline"
import type { Focus } from "./call-view"

// The conversation as a script: who said what, when, and what happened in between. The wait before each
// answer, the tools the agent ran and where it was cut off sit inline, where they happened.

function toolsIn(call: Call, from: number, to: number): Span[] {
  return (call.spans ?? []).filter((s) => s.name === "function_tool" && s.startMs !== null && s.startMs / 1000 >= from && s.startMs / 1000 <= to)
}
// A function_tool span as a tool call: arguments parsed when they are JSON, output kept as the agent got it.
function toolPart(s: Span) {
  const a = s.attrs ?? {}
  let input: Record<string, unknown> | undefined
  try {
    const v = JSON.parse(a["lk.pii.function_tool.arguments"] ?? "")
    input = v && typeof v === "object" ? v : { arguments: v }
  } catch {
    input = a["lk.pii.function_tool.arguments"] ? { arguments: a["lk.pii.function_tool.arguments"] } : undefined
  }
  const error = a["lk.function_tool.is_error"]?.toLowerCase() === "true"
  return {
    type: a["lk.function_tool.name"] ?? s.name,
    state: error ? ("output-error" as const) : ("output-available" as const),
    input,
    output: error ? undefined : (a["lk.pii.function_tool.output"] ?? undefined),
    errorText: error ? a["lk.pii.function_tool.output"] : undefined,
    toolCallId: a["lk.function_tool.id"],
    // Unreported duration: the tool card shows none rather than a number.
    duration: s.durationMs !== null ? fmtMs(s.durationMs) : undefined,
  }
}
const playedAt = (call: Call, i: number) => (call.spans ?? []).filter((s) => s.name === "agent_turn")[i]?.attrs?.["lk.playout.position"] ?? null

// Who is talking, at a glance: the agent in ink, the caller in grey.
function Who({ agent }: { agent?: boolean }) {
  return (
    <Avatar className="size-7">
      <AvatarFallback className={agent ? "bg-foreground text-background" : "bg-muted text-foreground"}>{agent ? <Robot size={15} weight="bold" /> : <User size={15} weight="bold" />}</AvatarFallback>
    </Avatar>
  )
}

export function CallScript({ call, model, selected, onSelect }: { call: Call; model: CallModel; selected: Focus; onSelect: (i: number, speaker: "caller" | "agent") => void }) {
  const player = useAudioPlayer()
  const now = useAudioPlayerTime()
  const rec = call.recording
  if (!model.turns.length) {
    return (
      <Empty className="py-16">
        <EmptyHeader>
          <EmptyTitle className="text-sm">No conversation in this call's data</EmptyTitle>
          <EmptyDescription className="text-xs">LiveKit reported the room, but no agent turns arrived for it.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  const go = (i: number, speaker: "caller" | "agent", at?: number) => {
    onSelect(i, speaker)
    if (rec && at !== undefined) player.seek(at)
  }
  const live = (a?: number, b?: number) => !!rec && player.isPlaying && a !== undefined && b !== undefined && now >= a && now < b

  return (
    <ol className="space-y-0.5 px-3 py-4">
      {model.turns.map((t) => {
        const on = (who: "caller" | "agent") => selected?.turn === t.i && (selected.speaker === null || selected.speaker === who)
        const tools = toolsIn(call, t.thinking?.start ?? t.utterance?.end ?? t.window.start, t.speaking?.end ?? t.window.end)
        const played = playedAt(call, t.i)
        const over = t.responseMs !== null && t.responseMs > BUDGET_MS
        const backchannels = model.moments.filter((m) => m.kind === "backchannel" && t.speaking && m.at.start >= t.speaking.start && m.at.start <= t.speaking.end)
        const row = "grid w-full grid-cols-[34px_28px_minmax(0,1fr)] items-start gap-2.5 rounded-xl px-2 py-2 text-left transition-colors"
        return (
          <li key={t.i} ref={selected?.turn === t.i ? (el) => el?.scrollIntoView({ block: "nearest", behavior: "smooth" }) : undefined} className="scroll-my-6">
            {t.userText !== null && (
              <button type="button" aria-pressed={on("caller")} onClick={() => go(t.i, "caller", t.utterance?.start)} className={cn(row, on("caller") ? "bg-accent ring-1 ring-primary/25 ring-inset" : "hover:bg-muted/60")}>
                <span className="pt-1.5 text-[11px] text-muted-foreground tnum">{t.utterance ? clock(t.utterance.start) : ""}</span>
                <Who />
                <span className="min-w-0">
                  <span className="flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
                    Caller
                    <span className="rounded-full px-1.5 text-[10px] tnum ring-1 ring-border" aria-label={`Turn ${t.i + 1}`}>{t.i + 1}</span>
                  </span>
                  <span className={cn("text-[15px] leading-snug", live(t.utterance?.start, t.utterance?.end) && "underline decoration-muted-foreground/60 decoration-2 underline-offset-4")}>{t.userText}</span>
                </span>
              </button>
            )}

            {(t.userText !== null && t.responseMs !== null) || tools.length > 0 ? (
              <div className="grid grid-cols-[34px_28px_minmax(0,1fr)] gap-2.5 px-2">
                <span />
                <span className="flex justify-center">
                  <span className="w-px self-stretch bg-border" />
                </span>
                <div className="space-y-1.5 py-1">
                  {t.userText !== null && t.responseMs !== null && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className={cn("flex w-fit cursor-help items-center gap-1.5 text-xs tnum", over ? "text-destructive" : "text-muted-foreground")}>
                          <ArrowBendDownRight size={13} />
                          {fmtMs(t.responseMs)}
                          {t.responseDerived ? " (derived)" : ""}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-72 text-xs">
                        {`Time to answer: the caller finishing to the agent's first audio${t.responseDerived ? ", derived from speech timing" : ""}.${t.eotMs !== null && t.eotMs > (t.responseMs ?? 0) * 0.5 ? ` ${fmtMs(t.eotMs)} of it was waiting for the end of the turn.` : ""}`}
                      </TooltipContent>
                    </Tooltip>
                  )}
                  {tools.map((s) => (
                    <Tool key={s.spanId} toolPart={toolPart(s)} className="mt-0 bg-background" />
                  ))}
                </div>
              </div>
            ) : null}

            <button type="button" aria-pressed={on("agent")} onClick={() => go(t.i, "agent", t.speaking?.start)} className={cn(row, on("agent") ? "bg-accent ring-1 ring-primary/25 ring-inset" : "hover:bg-muted/60")}>
              <span className="pt-1.5 text-[11px] text-muted-foreground tnum">{t.speaking ? clock(t.speaking.start) : ""}</span>
              <Who agent />
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-[11px] font-semibold text-foreground">
                  Agent
                  {t.userText === null && (
                    <>
                      <span className="rounded-full px-1.5 text-[10px] font-medium text-muted-foreground tnum ring-1 ring-border" aria-label={`Turn ${t.i + 1}`}>{t.i + 1}</span>
                      <span className="font-normal text-muted-foreground">greeting</span>
                    </>
                  )}
                  {t.interrupted && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="flex cursor-help items-center gap-1 font-medium text-destructive">
                          <Scissors size={12} weight="bold" />
                          Interrupted
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="text-xs">{`Cut off by the caller${played ? ` after ${Number(played).toFixed(1)} s of audio` : t.speaking ? ` after ${(t.speaking.end - t.speaking.start).toFixed(1)} s` : ""}.`}</TooltipContent>
                    </Tooltip>
                  )}
                  {backchannels.length > 0 && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="flex cursor-help items-center gap-1 font-normal text-muted-foreground">
                          <ChatTeardropDots size={12} />
                          Backchannel
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="text-xs">The caller said something mid-reply; LiveKit treated it as a backchannel and kept talking.</TooltipContent>
                    </Tooltip>
                  )}
                </span>
                <span className={cn("text-[15px] leading-snug", live(t.speaking?.start, t.speaking?.end) && "underline decoration-primary/50 decoration-2 underline-offset-4")}>{t.agentText ?? "Unknown"}</span>
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}
