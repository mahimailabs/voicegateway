import { Headset, Info, Phone, Robot } from "@phosphor-icons/react"
import { TimeRangePicker, useUrlRange } from "@/components/time-range-picker"
import { useUrlPick } from "@/lib/url-state"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { type GlobeArc, type GlobePoint, NetworkGlobe } from "@/components/network-globe"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { HardDrives } from "@phosphor-icons/react"
import { SourceStatus } from "@/components/source-status"
import { QueryState } from "@/components/page-state"
import { mediaServerQuery, accessDenied } from "@/lib/api"
import { type MediaServerData, type Participant, type Room, RoomDrawer, fromContract, kindIcon, kindName, openAtWatchEnd, roomOrigin } from "./room-drawer"

// The media server as the project's three keys see it (decision 0016): a few numbers, where people
// came in on a globe, and every room on one clock. Explanations live in tooltips, not on the page.
// Data exists only for the stretches we watched, so the clock is split into those stretches.

// LiveKit Cloud region codes seen in this project, by the city their name points to.
const regions: Record<string, { city: string; lat: number; lng: number }> = {
  oashburn1b: { city: "Ashburn", lat: 39.0438, lng: -77.4874 },
  osanjose1a: { city: "San Jose", lat: 37.3382, lng: -121.8863 },
  otoronto1a: { city: "Toronto", lat: 43.6532, lng: -79.3832 },
}

const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
const dur = (sec: number) => {
  const s = Math.round(sec)
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`
}

function Stat({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <div>
      <p className="text-[32px] leading-none font-semibold tracking-[-0.02em] tnum">{value}</p>
      <p className="mt-1.5 text-[13px] text-muted-foreground">{label}</p>
    </div>
  )
}

const GAP = 4 // percent of the axis between two watched stretches

export function MediaServerPage() {
  const read = useQuery(mediaServerQuery())
  const ms = useMemo(() => (read.data ? fromContract(read.data) : null), [read.data])
  if (!ms || accessDenied(read.error)) return <QueryState icon={HardDrives} what="the media server" query={read} />
  return <div className="space-y-4"><SourceStatus name="LiveKit SFU" source={read.data?.sourceStatus} interval={10000} query={read} /><MediaServerView data={ms} /></div>
}

function MediaServerView({ data }: { data: MediaServerData }) {
  const nodeRegion = data.server?.nodeId.split("_")[1]?.toLowerCase() ?? ""
  const node = regions[nodeRegion]
  // Only watches with both ends reported can be drawn on the clock.
  const windows = data.windows.filter((w): w is typeof w & { start: number; end: number } => w.start !== null && w.end !== null)
  const watched = windows.map((w) => `${hhmm(w.start)} to ${hhmm(w.end)}`).join(" and ")
  const allRooms: Room[] = data.rooms.map((r, k) => ({ ...r, n: k + 1 }))
  // "Now" is the end of the last watch, or the present when no watch has a known end.
  const now = windows.length ? windows[windows.length - 1].end : Date.now() / 1000
  const [range, setRange] = useUrlRange("6h", now)
  const [openRoom, setOpenRoom] = useUrlPick<Room>("room", allRooms, (r) => r.sid)

  // The axis: each watched stretch inside the range, sized by its length, with a small break between.
  const segs = windows.map((w) => ({ start: Math.max(w.start, range.from), end: Math.min(w.end, range.to) })).filter((s) => s.end > s.start)
  const total = segs.reduce((a, s) => a + s.end - s.start, 0)
  const usable = 100 - GAP * Math.max(segs.length - 1, 0)
  const layout = segs.map((s, k) => {
    const left = segs.slice(0, k).reduce((a, x) => a + ((x.end - x.start) / total) * usable + GAP, 0)
    return { ...s, left, width: ((s.end - s.start) / total) * usable }
  })
  const pct = (t: number) => {
    const s = layout.find((x) => t <= x.end) ?? layout[layout.length - 1]
    return s.left + ((Math.min(Math.max(t, s.start), s.end) - s.start) / (s.end - s.start)) * s.width
  }
  const covered = segs.length > 0
  const partial = !covered || range.from < segs[0].start - 60 || range.to > segs[segs.length - 1].end + 60 || segs.length > 1
  // A room is placed by when it was last seen and when it began (created, or first seen); a room with
  // neither reported cannot be placed and is left off the clock.
  const began = (r: Room) => (r.createdMs !== null ? r.createdMs / 1000 : r.firstSeen !== null ? r.firstSeen - 10 : null)
  const inRange = (r: Room) => r.lastSeen !== null && began(r) !== null && segs.some((s) => r.lastSeen! >= s.start && Math.max(began(r)!, (r.firstSeen ?? began(r)!) - 10) <= s.end)
  const rooms = covered ? allRooms.filter(inRange) : []
  const lifetimes = rooms.flatMap((r) => (r.createdMs !== null && r.lastSeen !== null ? [r.lastSeen - r.createdMs / 1000] : [])).filter((s) => s > 0)
  const avg = lifetimes.length ? lifetimes.reduce((a, b) => a + b, 0) / lifetimes.length : null
  const axisEnd = covered ? segs[segs.length - 1].end : now
  const atEnd = range.to >= now
  const live = rooms.filter((r) => r.lastSeen! >= axisEnd - 1).length
  const people = rooms.reduce((a, r) => a + r.participants.length, 0)

  const byRegion = new Map<string, Participant[]>()
  for (const p of rooms.flatMap((r) => r.participants)) if (p.region) byRegion.set(p.region, [...(byRegion.get(p.region) ?? []), p])
  const edges = [...byRegion.entries()].filter(([r]) => regions[r] && r !== nodeRegion)
  const atNode = byRegion.get(nodeRegion) ?? []
  const points: GlobePoint[] = [
    ...(node ? [{ ...node, label: `${node.city} · media server`, tone: "ink" as const }] : []),
    ...edges.map(([r, ps]) => ({ ...regions[r], label: `${regions[r].city} · ${kindName[ps[0].kind] ?? ps[0].kind}s enter here`, tone: "accent" as const })),
  ]
  const arcs: GlobeArc[] = node ? edges.map(([r]) => ({ from: regions[r], to: node, label: `${regions[r].city} to ${node.city}` })) : []

  // Ticks on round clock times inside each stretch, about six across the whole axis.
  const ticks = layout.flatMap((s) => {
    const want = Math.max(1, Math.round((6 * s.width) / 100))
    const step = [60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400].find((x) => (s.end - s.start) / x <= want) ?? 86400
    const first = Math.ceil(s.start / step)
    return Array.from({ length: Math.max(Math.floor(s.end / step) - first + 1, 0) }, (_, k) => (first + k) * step)
  })

  return (
    <div className="mx-auto max-w-[1200px] space-y-12">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Media server</h1>
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">
            {data.server ? `LiveKit ${data.server.edition} ${data.server.version}` : "LiveKit version unknown"}
          </span>
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">{node?.city ?? data.server?.region ?? "Region unknown"}</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" aria-label="How this was read" className="text-muted-foreground hover:text-foreground">
                <Info size={18} />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-96 text-xs leading-relaxed">{data.server
              ? `${data.source} Node ${data.server.nodeId}, protocol ${data.server.protocol ?? "unknown"}, joined in ${data.server.joinMs === null ? "an unreported time" : `${data.server.joinMs} ms`}.`
              : `${data.source} The server details were not reported.`}</TooltipContent>
          </Tooltip>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            {partial && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help text-[13px] text-muted-foreground">Partly watched</span>
                </TooltipTrigger>
                <TooltipContent className="max-w-72 text-xs">We watched {watched}. The keys show rooms that exist right now; rooms outside those times arrive with LiveKit's webhooks, once the VoiceGateway server runs.</TooltipContent>
              </Tooltip>
            )}
            <TimeRangePicker value={range} onChange={setRange} now={now} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat value={covered ? rooms.length : "Unknown"} label="rooms" />
          <Stat value={covered ? live : "Unknown"} label={atEnd ? "listed at last observation" : `listed at ${hhmm(axisEnd)}`} />
          <Stat value={covered ? people : "Unknown"} label="people joined" />
          <Stat value={avg !== null ? dur(avg) : "Unknown"} label="average room" />
        </div>
      </header>

      {rooms.length === 0 ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyTitle>No rooms in this range</EmptyTitle>
            <EmptyDescription>We watched {watched}. Pick a range that overlaps it.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <section className="grid items-center gap-8 lg:grid-cols-[420px_minmax(0,1fr)]">
            <div className="flex justify-center overflow-hidden rounded-2xl bg-muted/40">
              <NetworkGlobe points={points} arcs={arcs} />
            </div>
            <ul className="space-y-5">
              {node && (
                <li className="flex items-center gap-4">
                  <span className="size-3 rounded-full bg-foreground" />
                  <div className="flex-1">
                    <p className="text-[15px] font-medium">{node.city}</p>
                    <p className="text-[13px] text-muted-foreground">Media server{atNode.length ? ", and where nearby people connect" : ""}</p>
                  </div>
                  {atNode.length > 0 && <p className="text-[22px] font-semibold tnum">{atNode.length}</p>}
                </li>
              )}
              {edges.map(([r, ps]) => {
                const I = kindIcon[ps[0].kind] ?? Headset
                return (
                  <li key={r} className="flex items-center gap-4">
                    <span className="size-3 rounded-full bg-primary" />
                    <div className="flex-1">
                      <p className="text-[15px] font-medium">{regions[r].city}</p>
                      <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
                        <I size={14} />
                        {kindName[ps[0].kind] ?? ps[0].kind}s connect here
                      </p>
                    </div>
                    <p className="text-[22px] font-semibold tnum">{ps.length}</p>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="space-y-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-[15px] font-semibold">Every room, on one clock</h2>
              <span className="text-[12px] text-muted-foreground">Click a room for its tracks, participants and settings</span>
            </div>
            <div className="relative space-y-1.5">
              {/* breaks between watched stretches */}
              <div className="pointer-events-none absolute inset-y-0 right-0 left-[92px]">
                {layout.slice(1).map((s, k) => (
                  <Tooltip key={k}>
                    <TooltipTrigger asChild>
                      <span className="pointer-events-auto absolute inset-y-0 cursor-help bg-[repeating-linear-gradient(135deg,var(--border)_0_1px,transparent_1px_6px)]" style={{ left: `${s.left - GAP}%`, width: `${GAP}%` }} />
                    </TooltipTrigger>
                    <TooltipContent>{`Not watched for ${dur(s.start - layout[k].end)}`}</TooltipContent>
                  </Tooltip>
                ))}
              </div>
              {rooms.map((r) => {
                // Placed rooms have a known start and last-seen time (see inRange).
                const start = pct(Math.max(began(r)!, range.from))
                const end = pct(r.lastSeen!)
                const open = openAtWatchEnd(r, data)
                const failed = r.dispatches?.some((d) => d.jobs.some((j) => j.statuses.some((x) => x.status === "JS_FAILED"))) ?? false
                return (
                  <button
                    key={r.sid}
                    type="button"
                    onClick={() => setOpenRoom(r)}
                    className="group grid w-full grid-cols-[80px_minmax(0,1fr)] items-center gap-3 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground tnum group-hover:text-foreground">
                      Room {r.n}
                      {(() => {
                        const o = roomOrigin(r)
                        const I = o.kind === "phone-in" || o.kind === "phone-out" ? Phone : o.kind === "dispatch" ? Robot : null
                        return I && <I size={12} aria-label={o.label} className={o.kind === "dispatch" ? "text-muted-foreground" : "text-primary"} />
                      })()}
                    </span>
                    <div className="relative h-7">
                      <div
                        className={cn(
                          "absolute inset-y-0 flex min-w-7 items-center gap-1 rounded-full bg-primary/10 pr-2 pl-1 transition-colors group-hover:bg-primary/20",
                          open && "ring-1 ring-primary/40",
                          failed && "ring-1 ring-destructive/60",
                        )}
                        // Late rooms anchor to their end so the pill grows left and stays inside the track.
                        style={start > 50 ? { right: `${(100 - end).toFixed(2)}%`, minWidth: `max(${(end - start).toFixed(2)}%, 28px)` } : { left: `${start}%`, minWidth: `max(${(end - start).toFixed(2)}%, 28px)` }}
                      >
                        {r.participants.map((p) => {
                          const I = kindIcon[p.kind] ?? Headset
                          return (
                            <span key={p.sid} className={cn("flex size-5 shrink-0 items-center justify-center rounded-full", p.kind === "agent" ? "bg-primary text-primary-foreground" : "bg-background ring-1 ring-border")}>
                              <I size={11} weight="bold" />
                            </span>
                          )
                        })}
                        <span className="ml-auto pl-1 text-[11px] whitespace-nowrap text-foreground tnum">
                          {r.createdMs !== null ? dur(r.lastSeen! - r.createdMs / 1000) : "length unknown"}
                          {open ? "+" : ""}
                        </span>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
            <div className="grid grid-cols-[80px_minmax(0,1fr)] gap-3 text-[11px] text-muted-foreground tnum">
              <span />
              <div className="relative h-3">
                {ticks.map((t) => (
                  <span key={t} className={cn("absolute -translate-x-1/2 whitespace-nowrap", pct(t) < 4 && "translate-x-0", pct(t) > 96 && "-translate-x-full")} style={{ left: `${pct(t)}%` }}>
                    {hhmm(t)}
                  </span>
                ))}
              </div>
            </div>
          </section>
        </>
      )}

      <footer className="flex flex-wrap items-center gap-2 border-t pt-5 text-[13px] text-muted-foreground">
        <span>Not visible with the project's keys:</span>
        {[
          ["server load", "CPU, memory and bandwidth. Self-hosted servers expose them on their metrics port; LiveKit Cloud does not."],
          ["connection quality", "Packet loss and jitter per person. LiveKit Cloud analytics only."],
          ["older rooms", "Rooms before we started watching arrive with LiveKit's webhooks, once the VoiceGateway server runs."],
        ].map(([t, d]) => (
          <Tooltip key={t}>
            <TooltipTrigger asChild>
              <span className="cursor-help rounded-full border border-dashed px-2.5 py-0.5">{t}</span>
            </TooltipTrigger>
            <TooltipContent className="max-w-72 text-xs">{d}</TooltipContent>
          </Tooltip>
        ))}
      </footer>

      <RoomDrawer room={openRoom} ms={data} open={openRoom !== null} onOpenChange={(o) => !o && setOpenRoom(null)} />
    </div>
  )
}
