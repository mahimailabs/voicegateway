import { useQuery } from "@tanstack/react-query"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { type Call, providerProbesQuery } from "@/lib/api"
import type { ProviderProbes } from "@/lib/api-types"
import { useEmbedded } from "@/lib/url-state"
import { cn } from "@/lib/utils"
import { type CallModel, type Placed, type TurnModel, fmtMs, placed } from "@/features/call/call-model"

// Time to first audio for one turn, stage by stage on one clock, from the moment the caller stopped
// talking. Every bar is a real span or event of that turn. The hatched part of a model bar is the
// network round trip to that provider, measured separately from the agent's machine
// (getProviderProbes); the rest is the provider's own time. Without that read no part is hatched.
// Bars may overlap: the model can start before the turn is confirmed. A Gantt with hatched
// sub-segments has no library component, so it is laid out here.

type Row = { label: string; sub: string; start: number; end: number; hopMs?: number; kind: "speech" | "model" | "turn" | "hop" | "tool" }

const sec = (s: Placed) => s.startMs / 1000

export function waterfallRows(call: Call, t: TurnModel, probes: ProviderProbes | null): Row[] | null {
  const hop = (role: string) => probes?.endpoints.find((e) => e.role === role)?.tcpMs ?? null
  // Only spans with a start and duration can be drawn.
  const spans = (call.spans ?? []).filter(placed)
  if (!t.utterance || !spans.length) return null
  const t0 = t.utterance.end
  const inTurn = (s: Placed) => sec(s) >= t.utterance!.start - 0.5 && sec(s) <= (t.speaking?.start ?? t.window.end) + 0.2
  const rows: Row[] = []
  const td = call.turns?.[t.i]?.transcriptionDelayMs
  const stt = spans.find((s) => s.name === "user_turn" && inTurn(s))
  if (td != null) rows.push({ label: "Speech to text", sub: stt?.attrs?.["gen_ai.request.model"] ?? "STT", start: 0, end: td, hopMs: hop("stt") ?? undefined, kind: "model" })
  const det = spans.find((s) => s.name === "eou_detection" && inTurn(s))
  if (det) rows.push({ label: "Turn detection", sub: "end-of-turn model", start: (sec(det) - t0) * 1000, end: (sec(det) - t0) * 1000 + det.durationMs, kind: "turn" })
  if (t.eotMs !== null) rows.push({ label: "Turn confirmed", sub: "endpointing wait", start: 0, end: t.eotMs, kind: "turn" })
  const reqs = spans.filter((s) => s.name === "llm_request" && inTurn(s) && !s.attrs?.["gen_ai.response.finish_reasons"]?.includes("error"))
  reqs.forEach((r, k) => {
    const ttft = Number(r.attrs?.["gen_ai.response.time_to_first_chunk"] ?? NaN)
    const start = (sec(r) - t0) * 1000
    rows.push({ label: reqs.length > 1 ? `LLM, call ${k + 1}` : "LLM", sub: `${r.attrs?.["gen_ai.request.model"] ?? "model"} first token`, start, end: start + (Number.isFinite(ttft) ? ttft * 1000 : r.durationMs), hopMs: hop("llm") ?? undefined, kind: "model" })
    const tool = spans.find((s) => s.name === "function_tool" && sec(s) >= sec(r) && sec(s) <= sec(r) + (r.durationMs + 400) / 1000)
    if (tool) rows.push({ label: "Tool", sub: tool.attrs?.["lk.function_tool.name"] ?? "tool", start: (sec(tool) - t0) * 1000, end: (sec(tool) - t0) * 1000 + Math.max(tool.durationMs, 1), kind: "tool" })
  })
  const tts = spans.find((s) => s.name === "tts_node" && inTurn(s))
  const ttfb = Number(tts?.attrs?.["lk.response.ttfb"] ?? NaN)
  if (tts && Number.isFinite(ttfb)) rows.push({ label: "Text to speech", sub: `${tts.attrs?.["gen_ai.request.model"] ?? "TTS"} first audio`, start: (sec(tts) - t0) * 1000, end: (sec(tts) - t0) * 1000 + ttfb * 1000, hopMs: hop("tts") ?? undefined, kind: "model" })
  const sfu = hop("sfu")
  const firstAudio = t.e2eMs ?? (t.speaking ? (t.speaking.start - t0) * 1000 : null)
  if (sfu !== null && firstAudio !== null) rows.push({ label: "To the caller", sub: "agent to media server", start: firstAudio, end: firstAudio + sfu, kind: "hop" })
  return rows.length ? rows : null
}

const tone: Record<Row["kind"], string> = {
  model: "bg-primary",
  turn: "bg-foreground/35",
  tool: "bg-foreground/70",
  hop: "bg-foreground/15 ring-1 ring-inset ring-foreground/25",
  speech: "bg-muted-foreground",
}

export function TtfaWaterfall({ call, model, turn, compact }: { call: Call; model: CallModel; turn: number; compact?: boolean }) {
  // An embed token cannot read the probes, so an embedded call draws no network part.
  const embedded = useEmbedded()
  const probes = useQuery({ ...providerProbesQuery(), enabled: !embedded })
  const t = model.turns[turn]
  // Wait for the probes rather than redraw every bar when the network part arrives.
  if (!embedded && probes.isPending) return <p className="text-sm text-muted-foreground">Loading network timings.</p>
  const rows = t ? waterfallRows(call, t, probes.data ?? null) : null
  const gap = embedded ? "Network time is not shown in an embed, so model bars include it." : probes.isError ? "Network round trips could not be read, so model bars include network time." : null
  if (!t || !rows) return <p className="text-sm text-muted-foreground">This turn has no span timings to draw.</p>
  const max = Math.max(...rows.map((r) => r.end), t.e2eMs ?? 0) * (compact ? 1.35 : 1.2)
  const ticks = Array.from({ length: 6 }, (_, k) => Math.round(((max / 5) * k) / 50) * 50)
  const pct = (ms: number) => `${(Math.max(ms, 0) / max) * 100}%`
  const network = rows.reduce((a, r) => a + (r.hopMs ?? 0), 0)
  return (
    <div className="space-y-4">
      {gap && <p className="text-[12px] text-muted-foreground">{gap}</p>}
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
                  <div className={cn("absolute inset-y-1 flex items-center overflow-hidden rounded-md", tone[r.kind])} style={{ left: pct(r.start), width: `max(${pct(r.end - r.start)}, 6px)` }}>
                    {r.hopMs !== undefined && (
                      <span
                        className="h-full shrink-0 bg-[repeating-linear-gradient(135deg,rgb(255_255_255/0.55)_0_3px,transparent_3px_6px)]"
                        style={{ width: `${Math.min(100, (r.hopMs / Math.max(r.end - r.start, 1)) * 100)}%` }}
                      />
                    )}
                  </div>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  {`${r.label}: ${fmtMs(r.end - r.start)}, starting ${fmtMs(Math.max(r.start, 0))} after the caller stopped.`}
                  {r.hopMs !== undefined && ` About ${fmtMs(r.hopMs)} of it is the network round trip to the provider (measured from the agent's machine); the rest is the provider's own time.`}
                </TooltipContent>
              </Tooltip>
              <span className="absolute top-1/2 -translate-y-1/2 pl-1.5 text-[11px] font-medium whitespace-nowrap tnum" style={{ left: pct(r.end) }}>
                {fmtMs(r.end - r.start)}
                {r.hopMs !== undefined && <span className="font-normal text-muted-foreground"> · {fmtMs(r.hopMs)} network</span>}
              </span>
            </div>
          </div>
        ))}
        <div className={cn("grid gap-4 pt-1 text-[11px] text-muted-foreground tnum", compact ? "grid-cols-[120px_minmax(0,1fr)]" : "grid-cols-[150px_minmax(0,1fr)]")}>
          <span />
          <div className="relative h-3">
            {ticks.map((v, k) => (
              <span key={k} className={cn("absolute -translate-x-1/2", k === 0 && "translate-x-0")} style={{ left: pct(v) }}>{v} ms</span>
            ))}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-end justify-between gap-4 rounded-xl bg-muted/60 px-4 py-3">
        <div>
          <p className="text-[13px] font-medium">Time to first audio</p>
          <p className="text-xs text-muted-foreground tnum">
            {network > 0 ? `Network to providers about ${fmtMs(network)} · ` : ""}from the caller stopping to the agent's first sound
          </p>
        </div>
        <p className="text-[28px] leading-none font-semibold tracking-[-0.02em] tnum">{t.e2eMs !== null ? fmtMs(t.e2eMs) : t.responseMs !== null ? `~${fmtMs(t.responseMs)}` : "Unknown"}</p>
      </div>
    </div>
  )
}
