import { Info } from "@phosphor-icons/react"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { ChainStage } from "@/lib/api-types"
import { BUDGET_MS } from "@/lib/thresholds"
import { cn } from "@/lib/utils"
import { fmtMs, median } from "@/features/call/call-model"
import { useUrlState } from "@/lib/url-state"

// Every stage a turn passes through, caller's mouth to caller's ear, in order. Each stage is the median
// over the turns that reported it, with the LiveKit field it comes from. Stages LiveKit does not
// measure stay on the list as "not reported", so the measured ones are never read as the whole story.

type StageId = "join" | "callerIn" | "phone" | "nc" | "vad" | "stt" | "eou" | "eot" | "hook" | "llm1" | "tool" | "llm2" | "tts" | "rt" | "back" | "callerOut" | "stall"
type Stage = { id: StageId; label: string; source: string; why?: string }
const groups: { title: string; stages: Stage[] }[] = [
  {
    title: "Caller to agent",
    stages: [
      { id: "callerIn", label: "Caller's network", source: "not reported", why: "Only the caller's app sees its WebRTC stats; LiveKit does not pass them to the agent." },
      { id: "phone", label: "Phone leg", source: "not reported", why: "Carrier, phone network and transcoding. LiveKit reports call setup (ring to answer), not delay per turn." },
      { id: "nc", label: "Noise cancellation", source: "not reported", why: "Krisp runs on every audio frame; LiveKit exposes no timing for it." },
      { id: "vad", label: "Voice activity detection", source: "not reported", why: "VADMetrics reports inference time, not how long it waits to decide speech ended. That wait is the min silence setting." },
    ],
  },
  {
    title: "Ending the turn",
    stages: [
      { id: "stt", label: "Final transcript", source: "transcription_delay" },
      { id: "eou", label: "Turn detector", source: "eou_detection span" },
      { id: "eot", label: "Endpointing wait", source: "end_of_turn_delay", why: "From end of speech to the turn being called over. It includes the transcript and the turn detector above." },
      { id: "hook", label: "End-of-turn hook", source: "on_user_turn_completed", why: "Your own code before the model runs, such as a retrieval step." },
    ],
  },
  {
    title: "Answering",
    stages: [
      { id: "llm1", label: "Model first token", source: "llm ttft" },
      { id: "tool", label: "Tools", source: "function_tool spans" },
      { id: "llm2", label: "Model after tools", source: "second llm ttft" },
      { id: "tts", label: "Voice first audio", source: "tts ttfb" },
      { id: "rt", label: "Realtime model", source: "realtime ttft", why: "A speech-to-speech model replaces the transcript, turn detection, model and voice stages, and reports no breakdown." },
      { id: "back", label: "Agent to media server", source: "network probe", why: "Measured round trip from the agent host to LiveKit, not per turn." },
      { id: "callerOut", label: "Media server to caller", source: "not reported", why: "The caller's side of the return path; only their app sees it." },
    ],
  },
  {
    title: "Around the turn",
    stages: [
      { id: "stall", label: "Agent process stalls", source: "event_loop_blocked", why: "While the agent's event loop is blocked it cannot hear, think or speak." },
      { id: "join", label: "Agent join, per call", source: "dispatch to join", why: "Once per call, before the greeting: from LiveKit dispatching the job to the agent joining the room. Rises when the worker has to start a new process." },
    ],
  },
]

// One scope of the chain: every agent, or one. Stage figures come from getCallStats; the agent's join
// is per job (the fleet) and the hop back to the media server is the network probe.
export type ChainScope = { key: string; name: string; chain: ChainStage[]; turns: number | null; calls: number | null; e2e: number | null; joins: number[] }

export function LatencyChain({ all: overall, groups: sets, backMs }: { all: ChainScope; groups: ChainScope[]; backMs: number | null }) {
  const usable = sets.filter((g) => g.calls !== 0)
  const [pick, setPick] = useUrlState("agent", "all")
  const scope = pick === "all" ? overall : (usable.find((g) => g.key === pick) ?? overall)
  const joins = (pick === "all" ? sets.flatMap((g) => g.joins) : (sets.find((g) => g.key === pick)?.joins ?? [])).map((x) => x * 1000)
  const stat = (id: StageId) => {
    if (id === "join") return { n: joins.length, p50: median(joins), max: joins.length ? Math.max(...joins) : null }
    // The hop back is one measured round trip, the same for every turn.
    if (id === "back") return { n: backMs === null ? 0 : (scope.turns ?? 0), p50: backMs, max: backMs }
    const st = scope.chain.find((c) => c.stage === (id === "rt" ? "realtime" : id))
    return { n: st?.turns ?? 0, p50: st?.medianMs ?? null, max: st?.maxMs ?? null }
  }
  const e2e = scope.e2e
  const anyRealtime = scope.chain.some((c) => c.stage === "realtime")
  // The realtime row belongs to the agents that run a realtime model, named on the row.
  const rtNames = usable.filter((g) => (pick === "all" || g.key === pick) && g.chain.some((c) => c.stage === "realtime")).map((g) => g.name)
  const anyCascaded = scope.chain.some((c) => c.stage === "llm1" || c.stage === "eot")
  const shown = (st: Stage) => (st.id === "rt" ? anyRealtime : true)
  const scale =
    Math.max(
      e2e ?? 0,
      ...groups
        .flatMap((g) => g.stages)
        .filter((st) => st.id !== "join")
        .map((st) => stat(st.id).p50 ?? 0),
      1,
    ) * 1.05

  // The chain as LiveKit adds it up: endpointing, then the model, tools and voice, then the hop back.
  const chain = (["eot", "hook", "llm1", "tool", "llm2", "tts", "back"] as StageId[]).map((id) => ({ id, ms: stat(id).p50 })).filter((x): x is { id: StageId; ms: number } => x.ms !== null)
  const chainTotal = chain.reduce((a, x) => a + x.ms, 0)
  const chainTone: Partial<Record<StageId, string>> = { eot: "bg-muted-foreground/45", hook: "bg-muted-foreground/70", llm1: "bg-primary", tool: "bg-foreground/70", llm2: "bg-primary/75", tts: "bg-primary/50", back: "bg-primary/25" }
  const label = (id: StageId) => groups.flatMap((g) => g.stages).find((s) => s.id === id)!.label

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ToggleGroup type="single" variant="outline" size="sm" value={pick} onValueChange={(v) => v && setPick(v)} aria-label="Agent">
          <ToggleGroupItem value="all" className="px-3 data-[state=on]:bg-accent">
            All agents
          </ToggleGroupItem>
          {usable.map((g) => (
            <ToggleGroupItem key={g.key} value={g.key} className="px-3 data-[state=on]:bg-accent">
              {g.name}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <span className="text-[12px] text-muted-foreground tnum">{`${scope.turns ?? "unknown"} turns · ${scope.calls ?? "unknown"} calls`}</span>
      </div>

      {anyCascaded && chain.length > 0 && (
        <div className="space-y-2 rounded-xl border bg-muted/30 p-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[13px] font-medium">Stage medians, end to end</span>
            <span className="text-[13px] tnum">
              <span className="text-muted-foreground">measured end to end </span>
              <span className={cn("text-[17px] font-semibold", e2e !== null && e2e > BUDGET_MS && "text-destructive")}>{e2e === null ? "unknown" : fmtMs(e2e)}</span>
            </span>
          </div>
          <div className="relative">
            <div className="flex h-4 overflow-hidden rounded-md" style={{ width: `${(chainTotal / Math.max(chainTotal, e2e ?? 0)) * 100}%` }}>
              {chain.map((x) => (
                <Tooltip key={x.id}>
                  <TooltipTrigger asChild>
                    <span className={cn("h-full border-r border-background/60 last:border-r-0", chainTone[x.id])} style={{ flex: x.ms }} />
                  </TooltipTrigger>
                  <TooltipContent className="text-xs">{`${label(x.id)}: ${fmtMs(x.ms)}`}</TooltipContent>
                </Tooltip>
              ))}
            </div>
            {e2e !== null && <span className="absolute -top-1 -bottom-1 w-0.5 rounded bg-foreground" style={{ left: `${(e2e / Math.max(chainTotal, e2e)) * 100}%` }} title="Measured end to end" />}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {chain.map((x) => (
              <span key={x.id} className="flex items-center gap-1.5">
                <span className={cn("size-2 rounded-sm", chainTone[x.id])} />
                {`${label(x.id)} ${fmtMs(x.ms)}`}
              </span>
            ))}
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-0.5 rounded bg-foreground" />
              measured end to end
            </span>
          </div>
        </div>
      )}

      <div className="grid items-start gap-5 lg:grid-cols-2">
        {[["Ending the turn", "Around the turn"], ["Answering"]].map((col) => (
          <div key={col[0]} className="space-y-5">
            {groups
              .filter((g) => col.includes(g.title))
              .map((g) => (
                <div key={g.title}>
                  <p className="pb-1.5 text-[12px] font-medium text-muted-foreground">{g.title}</p>
                  {g.stages.some((st) => st.source !== "not reported") && (
                    <ul className="divide-y rounded-xl border">
                      {g.stages
                        .filter(shown)
                        .filter((st) => st.source !== "not reported")
                        .map((st) => {
                          const s = stat(st.id)
                          const reported = st.source !== "not reported"
                          return (
                            <li key={st.id} className={cn("grid grid-cols-[minmax(0,1fr)_96px_84px] items-center gap-3 px-3.5 py-2", !reported && "opacity-60")}>
                              <span className="min-w-0">
                                <span className="flex items-center gap-1.5 text-[13px] font-medium">
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <span className="cursor-help truncate">{st.id === "rt" && rtNames.length ? `${st.label} · ${rtNames.join(", ")}` : st.label}</span>
                                    </TooltipTrigger>
                                    <TooltipContent className="text-xs">{`${st.source}${s.n ? ` · ${st.id === "back" ? "network probe" : st.id === "join" ? `${s.n} jobs` : st.id === "stall" ? `${s.n} turns with a stall` : `${s.n} of ${scope.turns ?? "an unknown number of"} turns`}` : ""}`}</TooltipContent>
                                  </Tooltip>
                                  {st.why && (
                                    <Tooltip>
                                      <TooltipTrigger asChild>
                                        <button type="button" aria-label={`About ${st.label}`} className="text-muted-foreground hover:text-foreground">
                                          <Info size={13} />
                                        </button>
                                      </TooltipTrigger>
                                      <TooltipContent className="max-w-72 text-xs leading-relaxed">{st.why}</TooltipContent>
                                    </Tooltip>
                                  )}
                                </span>
                              </span>
                              <span className="h-2 rounded-full bg-muted">
                                {s.p50 !== null ? (
                                  <span
                                    className={cn(
                                      "block h-full rounded-full",
                                      st.id === "stall" ? "bg-destructive" : st.id === "join" ? "bg-amber-500" : st.id === "eot" || st.id === "eou" || st.id === "stt" ? "bg-muted-foreground/50" : "bg-primary",
                                    )}
                                    style={{ width: `${Math.min(Math.max((s.p50 / scale) * 100, 1.5), 100)}%` }}
                                  />
                                ) : (
                                  <span className="block h-full rounded-full border border-dashed border-muted-foreground/40 bg-transparent" />
                                )}
                              </span>
                              <span className="text-right">
                                {s.p50 !== null ? (
                                  <>
                                    <span className={cn("block text-[13px] font-semibold tnum", st.id === "stall" && "text-destructive")}>{st.id === "stall" ? `up to ${fmtMs(s.max!)}` : fmtMs(s.p50)}</span>
                                  </>
                                ) : (
                                  <span className="text-[12px] text-muted-foreground">{reported ? "none in these turns" : "not reported"}</span>
                                )}
                              </span>
                            </li>
                          )
                        })}
                    </ul>
                  )}
                </div>
              ))}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
        <span className="text-muted-foreground">Not reported by LiveKit</span>
        {groups
          .flatMap((g) => g.stages)
          .filter((st) => st.source === "not reported")
          .map((st) => (
            <Tooltip key={st.id}>
              <TooltipTrigger asChild>
                <span className="cursor-help rounded-full border border-dashed px-2 py-0.5 text-muted-foreground">{st.label}</span>
              </TooltipTrigger>
              <TooltipContent className="max-w-72 text-xs leading-relaxed">{st.why}</TooltipContent>
            </Tooltip>
          ))}
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Each stage is its own median over the turns that reported it, so stages need not add up to the measured end to end. The endpointing wait includes the transcript and the turn detector.
      </p>
    </div>
  )
}
