import { useEffect, useMemo, useRef, useState } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowsOutLineHorizontal, ChatTeardropDots, Microphone, Scissors, Wrench } from "@phosphor-icons/react"
import { AudioPlayerButton, AudioPlayerSpeed, AudioPlayerTime, useAudioPlayer, useAudioPlayerTime } from "@/components/elevenlabs/audio-player"
import { Waveform } from "@/components/elevenlabs/waveform"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Unknown } from "@/components/unknown"
import type { Call } from "@/lib/api"
import { cn } from "@/lib/utils"
import { type CallModel, type Iv, callLength, fmtMs, fmtSec } from "./call-model"
import { BUDGET_MS } from "./call-steps"
import { useEmbedded } from "@/lib/url-state"
import { useQuery } from "@tanstack/react-query"
import { playableUrl } from "@/lib/embed"

// The call as one stereo track: caller above the line, agent below, on one clock. The silence between the
// caller finishing and the agent answering is drawn and labelled, because that gap is what callers feel.
// Interval lanes over waveforms have no library component, so the track is laid out here; the waveform
// itself is ElevenLabs UI's.

const cssVar = (name: string) => (typeof window === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(name).trim())
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`
const move = "transition-[left,width] duration-200 ease-out"

function scale(w: Iv) {
  const span = Math.max(w.end - w.start, 0.001)
  const pct = (t: number) => ((t - w.start) / span) * 100
  return {
    left: (t: number) => `${pct(Math.max(t, w.start))}%`,
    width: (iv: Iv) => `${Math.max(pct(Math.min(iv.end, w.end)) - pct(Math.max(iv.start, w.start)), 0)}%`,
    in: (iv: Iv) => iv.end > w.start && iv.start < w.end,
    at: (t: number) => t >= w.start && t <= w.end,
  }
}

function Wave({ data, duration, w, color, flip }: { data: number[]; duration: number; w: Iv; color: string; flip?: boolean }) {
  const slice = useMemo(() => {
    const a = Math.max(0, Math.floor((w.start / duration) * data.length))
    const b = Math.min(data.length, Math.ceil((w.end / duration) * data.length))
    return data.slice(a, Math.max(b, a + 1))
  }, [data, duration, w.start, w.end])
  return (
    <div className={cn("pointer-events-none absolute inset-x-0 h-1/2", flip ? "bottom-0" : "top-0")}>
      <Waveform data={slice} height="100%" barWidth={2} barGap={1.5} barRadius={1} barColor={color} fadeEdges={false} className="h-full" />
    </div>
  )
}

// selected highlights a turn; zoom narrows the track to one, which only the timeline itself asks for.
export function CallTimeline({ call, model, selected, zoom, onSelect }: { call: Call; model: CallModel; selected: number | null; zoom: number | null; onSelect: (i: number | null) => void }) {
  const rec = call.recording
  const embedded = useEmbedded()
  // In a widget framed on another site the audio is read with the embed token (lib/embed.ts).
  // A blob: address holds the whole recording in memory: free it when the call or the view goes,
  // and keep none in the cache, where a freed one could come back.
  const playable = useQuery({ queryKey: ["recording-src", rec?.url], queryFn: () => playableUrl(rec!.url), enabled: !!rec, staleTime: Infinity, gcTime: 0 })
  useEffect(() => {
    const src = playable.data
    return () => {
      if (src?.startsWith("blob:")) URL.revokeObjectURL(src)
    }
  }, [playable.data])
  const player = useAudioPlayer()
  const now = useAudioPlayerTime()
  const whole: Iv = { start: 0, end: Math.max(...[callLength(call, model), model.duration].filter((x): x is number => x !== null), 0.5) }
  const length = callLength(call, model)
  const w = zoom !== null && model.turns[zoom] ? model.turns[zoom].window : whole
  const s = scale(w)
  const ticks = Array.from({ length: 7 }, (_, k) => w.start + ((w.end - w.start) * k) / 6)
  const gaps = model.turns
    .filter((t) => t.utterance && t.speaking && t.speaking.start > t.utterance.end)
    .map((t) => ({ i: t.i, iv: { start: t.utterance!.end, end: t.speaking!.start }, ms: t.responseMs, derived: t.responseDerived }))
  const turnAt = (time: number) => model.turns.findIndex((t) => time >= t.window.start && time <= t.window.end)
  const onTrack = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const time = w.start + ((e.clientX - r.left) / r.width) * (w.end - w.start)
    if (rec) player.seek(time)
    const i = turnAt(time)
    if (selected === null && i >= 0) onSelect(i)
  }
  const head = rec && (player.activeItem || now > 0) && s.at(now) ? s.left(now) : null
  const callerColor = cssVar("--muted-foreground") || "#6e6e73"
  const agentColor = cssVar("--primary") || "#0071e3"
  // Pins are small icons; one that would touch its neighbour drops to the next row.
  const pinBox = useRef<HTMLDivElement>(null)
  const [boxW, setBoxW] = useState(1000)
  useEffect(() => {
    const el = pinBox.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setBoxW(e.contentRect.width || 1000))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const pins = model.moments.filter((m) => "at" in m && s.in(m.at)).sort((a, b) => ("at" in a ? a.at.start : 0) - ("at" in b ? b.at.start : 0))
  const span = w.end - w.start
  const rows: number[] = []
  const ends: number[] = []
  for (const m of pins) {
    if (!("at" in m)) {
      rows.push(0)
      continue
    }
    const x = ((m.at.start - w.start) / span) * boxW
    let r = ends.findIndex((e) => x - 9 >= e + 2)
    if (r < 0) r = ends.length
    ends[r] = x + 9
    rows.push(r)
  }
  const lanes = Math.max(1, ...rows.map((r) => r + 1))

  return (
    <div className="space-y-2 px-6 pt-3 pb-3">
      <div className="flex flex-wrap items-center gap-3">
        {rec ? (
          <>
            <AudioPlayerButton item={{ id: call.id, src: playable.data ?? rec.url }} disabled={!playable.data} variant="default" size="icon-sm" className="rounded-full" />
            <span className="text-sm tnum"><AudioPlayerTime className="font-medium" /> <span className="text-muted-foreground">/ {length !== null ? clock(length) : <Unknown why="Neither the room nor the call's timeline reported a length" />}</span></span>
            <AudioPlayerSpeed variant="ghost" size="icon-sm" />
          </>
        ) : embedded ? (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground"><Microphone /> Recordings off</span>
        ) : (
          <Button asChild variant="ghost" size="sm" className="-ml-2 text-xs text-muted-foreground">
            <Link to="/settings"><Microphone /> Recordings off</Link>
          </Button>
        )}
        <div className="ml-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-1 rounded-full bg-muted-foreground" />Caller</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-1 rounded-full bg-primary" />Agent</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-3 rounded-[2px] bg-foreground/[0.07] ring-1 ring-foreground/15 ring-inset" />Wait for an answer</span>
          {pins.some((m) => m.kind === "interruption") && <span className="flex items-center gap-1"><Scissors size={12} weight="bold" className="text-destructive" />Interrupted</span>}
          {pins.some((m) => m.kind === "backchannel") && <span className="flex items-center gap-1"><ChatTeardropDots size={12} />Backchannel</span>}
          {pins.some((m) => m.kind === "tool") && <span className="flex items-center gap-1"><Wrench size={12} />Tool call</span>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {zoom !== null && (
            <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => onSelect(null)}>
              <ArrowsOutLineHorizontal /> Whole call
            </Button>
          )}
        </div>
      </div>

      {/* Moments: tools, interruptions and backchannels pinned above the track as icons; the legend names them */}
      <div ref={pinBox} className="relative" style={{ height: lanes * 18 }}>
        {pins.map((m, k) => {
          if (!("at" in m)) return null
          const I = m.kind === "tool" ? Wrench : m.kind === "interruption" ? Scissors : ChatTeardropDots
          return (
            <Tooltip key={k}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={m.kind === "tool" ? `Tool call ${m.name}` : m.kind === "interruption" ? "Interrupted" : "Backchannel"}
                  onClick={() => rec && player.seek(m.at.start)}
                  className={cn("absolute top-0 flex size-4 -translate-x-1/2 items-center justify-center rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none", move, m.kind === "interruption" ? "text-destructive" : "text-muted-foreground")}
                  style={{ left: s.left(m.at.start), top: rows[k] * 18 }}
                >
                  <I size={14} weight={m.kind === "interruption" ? "bold" : "regular"} />
                </button>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">
                {m.kind === "tool"
                  ? `${m.name} ran for ${fmtMs((m.at.end - m.at.start) * 1000)}`
                  : m.kind === "interruption"
                    ? `Interrupted: the caller talked over the agent; it stopped ${m.detectionMs !== null ? `${fmtSec(m.detectionMs / 1000)} later` : "after an unreported delay"}.${m.inferred ? " Inferred from the reply's interrupted flag." : ""}`
                    : `Backchannel: the caller spoke while the agent talked; LiveKit kept talking${m.detectionMs !== null ? ` (detected in ${fmtSec(m.detectionMs / 1000)})` : ""}.`}
              </TooltipContent>
            </Tooltip>
          )
        })}
      </div>

      {/* The stereo track */}
      <div className="relative h-14 cursor-pointer overflow-hidden rounded-xl bg-muted/50" onClick={onTrack} role="presentation">
        <div className="pointer-events-none absolute inset-x-0 top-1/2 h-px bg-border" />
        {model.caller.map((iv, k) => s.in(iv) && <div key={`c${k}`} className={cn("pointer-events-none absolute top-1 h-[calc(50%-4px)] rounded-md bg-foreground/[0.06]", move)} style={{ left: s.left(iv.start), width: s.width(iv) }} />)}
        {model.speaking.map((iv, k) => s.in(iv) && <div key={`a${k}`} className={cn("pointer-events-none absolute bottom-1 h-[calc(50%-4px)] rounded-md bg-primary/10", move)} style={{ left: s.left(iv.start), width: s.width(iv) }} />)}
        {rec?.peaks && rec.duration !== null && <Wave data={rec.peaks.caller} duration={rec.duration} w={w} color={callerColor} />}
        {rec?.peaks && rec.duration !== null && <Wave data={rec.peaks.agent} duration={rec.duration} w={w} color={agentColor} flip />}
        {!rec && (
          <>
            {model.caller.map((iv, k) => s.in(iv) && <div key={`cb${k}`} className={cn("pointer-events-none absolute top-[22%] h-[12%] rounded-full bg-muted-foreground/60", move)} style={{ left: s.left(iv.start), width: s.width(iv) }} />)}
            {model.speaking.map((iv, k) => s.in(iv) && <div key={`ab${k}`} className={cn("pointer-events-none absolute bottom-[22%] h-[12%] rounded-full bg-primary", move)} style={{ left: s.left(iv.start), width: s.width(iv) }} />)}
          </>
        )}
        {gaps.map((g) => {
          if (!s.in(g.iv)) return null
          const over = g.ms !== null && g.ms > BUDGET_MS
          return (
            <Tooltip key={`g${g.i}`}>
              <TooltipTrigger asChild>
                <div
                  className={cn("absolute inset-y-0 flex items-center justify-center bg-foreground/[0.05] ring-1 ring-foreground/10 ring-inset", move)}
                  style={{ left: s.left(g.iv.start), width: s.width(g.iv) }}
                >
                  <span className={cn("rounded-full bg-background px-1.5 py-px text-[11px] font-medium whitespace-nowrap tnum shadow-[0_1px_2px_rgb(0_0_0/0.08)]", over && "text-destructive")}>
                    {g.ms !== null ? fmtMs(g.ms) : fmtSec(g.iv.end - g.iv.start)}
                  </span>
                </div>
              </TooltipTrigger>
              <TooltipContent>{`Turn ${g.i + 1}: ${g.ms !== null ? `${g.derived ? "about " : ""}${fmtMs(g.ms)} from the caller finishing to the agent answering${g.derived ? " (derived from speech timing)" : " (measured end to end)"}` : "no response time reported"}. Budget ${fmtMs(BUDGET_MS)}.`}</TooltipContent>
            </Tooltip>
          )
        })}
        {model.moments.map((m, k) =>
          (m.kind === "interruption" || m.kind === "backchannel") && s.in(m.at) ? (
            <div key={`m${k}`} className={cn("pointer-events-none absolute inset-y-0 min-w-0.5", move, m.kind === "interruption" ? "bg-destructive/15 border-x border-destructive/60" : "bg-foreground/5 border-x border-foreground/25")} style={{ left: s.left(m.at.start), width: s.width(m.at) }} />
          ) : null,
        )}
        {head && <span className="pointer-events-none absolute inset-y-0 z-10 w-0.5 rounded-full bg-foreground" style={{ left: head }} />}
      </div>

      {/* Turn ruler and clock */}
      <div className="relative h-5">
        {model.turns.map((t) => {
          const a = t.utterance?.start ?? t.thinking?.start ?? t.speaking?.start
          if (a === undefined || !s.at(a)) return null
          const on = selected === t.i
          return (
            <button
              key={t.i}
              type="button"
              onClick={() => onSelect(on ? null : t.i)}
              aria-label={`Turn ${t.i + 1}`}
              aria-pressed={on}
              className={cn(
                "absolute top-0 flex h-5 min-w-5 -translate-x-1/2 items-center justify-center rounded-full px-1.5 text-[11px] font-medium tnum transition-colors",
                move,
                on ? "bg-primary text-primary-foreground" : "bg-background text-foreground ring-1 ring-border hover:ring-foreground/40",
                t.interrupted && !on && "text-destructive ring-destructive/40",
              )}
              style={{ left: s.left(a) }}
            >
              {t.i + 1}
            </button>
          )
        })}
      </div>
      <div className="relative h-3 text-[11px] text-muted-foreground tnum">
        {ticks.map((x, k) => (
          <span key={k} className={cn("absolute -translate-x-1/2", k === 0 && "translate-x-0", k === ticks.length - 1 && "-translate-x-full")} style={{ left: `${(k / (ticks.length - 1)) * 100}%` }}>
            {clock(x)}
          </span>
        ))}
      </div>
    </div>
  )
}
