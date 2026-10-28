import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { ArrowCounterClockwise, Broadcast, Broom, CaretDown, MagnifyingGlass, MagnifyingGlassMinus, MagnifyingGlassPlus, Phone, Robot, Users } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { callInRoomQuery, mediaServerQuery, accessDenied } from "@/lib/api"
import { SourceStatus, useObservationClock } from "@/components/source-status"
import { observedState } from "@/lib/live-status"
import { cn } from "@/lib/utils"
import { type MediaServerData, type Room, RoomDrawer, fromContract, roomOrigin } from "../media-server/room-drawer"
import { useUrlPick, useUrlState } from "@/lib/url-state"

// The timeline uses original observation timestamps. Failed reads never imply a closed room.

const windows = [
  { s: 300, short: "5m", long: "Last 5 minutes", tick: 60 },
  { s: 900, short: "15m", long: "Last 15 minutes", tick: 180 },
  { s: 1800, short: "30m", long: "Last 30 minutes", tick: 300 },
  { s: 3600, short: "1h", long: "Last hour", tick: 600 },
  { s: 21600, short: "6h", long: "Last 6 hours", tick: 3600 },
  { s: 43200, short: "12h", long: "Last 12 hours", tick: 7200 },
  { s: 86400, short: "1d", long: "Last day", tick: 14400 },
]

type Timed = Room & { createdMs: number; lastSeen: number }
function observedRooms(ms: MediaServerData): Timed[] {
  return ms.rooms.map((r, k) => ({ ...r, n: k + 1 }))
    .filter((r): r is Timed => r.createdMs !== null && r.lastSeen !== null)
}

const hms = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
const hm = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
const length = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s` : `${Math.floor(s)}s`)

// The real clock, a few times a second, so bars and ticks glide rather than jump.
function useNow(running: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now() / 1000), 250)
    return () => clearInterval(id)
  }, [running])
  return now
}

// The rooms come from the media server read (getMediaServer).
export function LivePage() {
  const read = useQuery(mediaServerQuery())
  const ms = useMemo(() => (read.data ? fromContract(read.data) : null), [read.data])
  if (!ms || accessDenied(read.error)) return <QueryState icon={Broadcast} what="the rooms" query={read} />
  return <div className="space-y-4">
    <SourceStatus name="LiveKit SFU" source={read.data?.sourceStatus} interval={10000} query={read} />
    <LiveView ms={ms} disconnected={read.isError || read.fetchStatus === "paused"} />
  </div>
}

function LiveView({ ms, disconnected }: { ms: MediaServerData; disconnected: boolean }) {
  const captured = useMemo(() => observedRooms(ms), [ms])
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [running, setRunning] = useState(true)
  const [wi, setWi] = useState(0)
  const [q, setQ] = useUrlState("search", "")
  const [openRoom, setOpenRoom] = useUrlPick<Room>("room", captured, (r) => r.sid)
  // A dragged range on the time strip pauses the stream and shows just that range.
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)
  const [clearedAt, setClearedAt] = useState<number | null>(null)
  const [headers, setHeaders] = useState(false)
  const strip = useRef<HTMLDivElement>(null)
  const filter = useRef<HTMLInputElement>(null)
  const clockNow = useNow(running && !range)
  const observationNow = useObservationClock()
  const w = windows[wi]
  const now = range ? range.to : clockNow
  const span = range ? range.to - range.from : w.s
  const from = now - span
  // "/" focuses the filter, as in Logfire.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.key === "/" && !(t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable))) {
        e.preventDefault()
        filter.current?.focus()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  const atX = (clientX: number) => {
    const r = strip.current!.getBoundingClientRect()
    // The ticks sit inside the strip's 16 px padding, so measure against that inner width.
    return Math.min(Math.max((clientX - r.left - 16) / (r.width - 32), 0), 1)
  }

  const rows = captured.map((room) => ({
    key: room.sid, room, start: room.createdMs / 1000, end: room.lastSeen,
    state: observedState(room.lastSeen * 1000, ms.sourceStatus, 10000, observationNow, disconnected),
  })).filter((r) => r.start <= now && r.end >= from)
  const shown = rows
    .filter((r) => clearedAt === null || r.start >= clearedAt)
    .filter((r) => r.room.name.toLowerCase().includes(q.trim().toLowerCase()))
    .sort((a, b) => b.start - a.start)
  const live = shown.filter((r) => r.state === "Observed active").length
  const x = (s: number) => ((Math.min(Math.max(s, from), now) - from) / span) * 100
  const ticks: number[] = []
  const step = range ? (windows.find((o) => o.s >= span * 0.9) ?? windows[windows.length - 1]).tick / (span < 300 ? 5 : 1) : w.tick
  for (let s = Math.ceil(from / step) * step; s < now - span * 0.06; s += step) if (s > from + span * 0.06) ticks.push(s)
  const tickLabel = span < 3600 ? hms : hm
  // A room that is a call opens the call, matched by the room's SID; any other room, or a lookup that
  // fails, opens the room drawer.
  // Only the latest click acts, and nothing acts once the page is left.
  const lastClick = useRef(0)
  useEffect(() => () => void (lastClick.current = -1), [])
  const open = async (r: Room) => {
    const mine = ++lastClick.current
    const callId = await queryClient.fetchQuery(callInRoomQuery(r.name, r.sid)).catch(() => null)
    if (lastClick.current !== mine) return
    if (callId) void navigate({ to: "/calls/$callId", params: { callId } })
    else setOpenRoom(r)
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <InputGroup className="w-72">
          <InputGroupAddon>
            <MagnifyingGlass />
          </InputGroupAddon>
          <InputGroupInput ref={filter} placeholder="Filter rooms" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter rooms" />
          <InputGroupAddon align="inline-end">
            <Kbd>/</Kbd>
          </InputGroupAddon>
        </InputGroup>
        <div className="mx-auto flex items-center gap-1 rounded-lg border px-2 py-1">
          <span className="px-2 text-[12px] text-muted-foreground tnum">Total {range ? length(span) : w.short}</span>
          <Button variant="ghost" size="icon-xs" onClick={() => setWi(Math.max(0, wi - 1))} disabled={wi === 0} aria-label="Zoom in">
            <MagnifyingGlassPlus />
          </Button>
          <Button variant="ghost" size="icon-xs" onClick={() => setWi(Math.min(windows.length - 1, wi + 1))} disabled={wi === windows.length - 1} aria-label="Zoom out">
            <MagnifyingGlassMinus />
          </Button>
        </div>
        {range && (
          <Button variant="outline" size="sm" onClick={() => setRange(null)} className="gap-1.5">
            <ArrowCounterClockwise /> Back to now
          </Button>
        )}
        <div className="flex items-center">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="w-40 justify-between rounded-r-none">
                {w.long} <CaretDown />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuRadioGroup value={String(wi)} onValueChange={(v) => (setRange(null), setWi(Number(v)))}>
                {windows.map((o, k) => (
                  <DropdownMenuRadioItem key={o.s} value={String(k)}>
                    {o.long}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="sm" onClick={() => (range ? setRange(null) : setRunning(!running))} aria-pressed={running && !range} className="-ml-px gap-2 rounded-l-none">
            <span className={cn("size-2 rounded-full", running && !range ? "animate-pulse bg-primary" : "bg-muted-foreground")} />
            {running && !range ? "Following" : "Paused"}
          </Button>
        </div>
      </div>

      {/* The time strip: window length on top, Now at the right edge, round ticks sliding left. */}
      <div
        ref={strip}
        className="relative cursor-crosshair rounded-xl border px-4 pt-1.5 pb-2 select-none"
        title="Drag to zoom into a range"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          const a = atX(e.clientX)
          setDrag({ a, b: a })
        }}
        onPointerMove={(e) => drag && setDrag({ ...drag, b: atX(e.clientX) })}
        onPointerUp={() => {
          if (drag && Math.abs(drag.b - drag.a) > 0.02) {
            const [lo, hi] = [Math.min(drag.a, drag.b), Math.max(drag.a, drag.b)]
            setRange({ from: from + lo * span, to: from + hi * span })
          }
          setDrag(null)
        }}
      >
        {drag && (
          <span
            className="pointer-events-none absolute inset-y-0 rounded-md bg-primary/10 ring-1 ring-primary/30"
            style={{ left: `calc(16px + (100% - 32px) * ${Math.min(drag.a, drag.b)})`, width: `calc((100% - 32px) * ${Math.abs(drag.b - drag.a)})` }}
          />
        )}
        <div className="relative h-4 text-[11px] text-muted-foreground">
          <span className="absolute left-1/2 -translate-x-1/2">{range ? `${hms(range.from)} to ${hms(range.to)}` : w.short}</span>
          <span className="absolute right-0">{range ? "" : "Now"}</span>
        </div>
        <div className="relative h-2">
          <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
          {ticks.map((s) => (
            <span key={s} className="absolute top-0 h-2 w-px bg-border" style={{ left: `${x(s)}%` }} />
          ))}
        </div>
        <div className="relative h-4 text-[11px] text-muted-foreground tnum">
          <span className="absolute left-0">{hms(from)}</span>
          {ticks.map((s) => (
            <span key={s} className="absolute -translate-x-1/2" style={{ left: `${x(s)}%` }}>
              {tickLabel(s)}
            </span>
          ))}
          <span className="absolute right-0">{hms(now)}</span>
        </div>
      </div>

      <div className="flex items-center gap-4 px-1 text-[12px] text-muted-foreground tnum">
        <span>
          {live} observed active · {shown.length} in view
        </span>
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-[12px] text-muted-foreground" onClick={() => setClearedAt(clearedAt === null ? now : null)}>
          <Broom /> {clearedAt === null ? "Clear" : `Cleared at ${hms(clearedAt)} · Show all`}
        </Button>
        <label className="flex items-center gap-2">
          Column headers
          <Switch checked={headers} onCheckedChange={setHeaders} className="data-[state=checked]:bg-foreground" />
        </label>
        <span className="ml-auto">Observed timestamps · bars end at the last sample{ms.rooms.length > captured.length && ` · ${ms.rooms.length - captured.length} rooms lack timestamps`}</span>
      </div>

      <div className="rounded-xl border">
        {shown.length === 0 ? (
          <div className="space-y-3 py-16 text-center">
            <p className="text-[13px] text-muted-foreground tnum">
              No rooms from {hms(from)} to {hms(now)}
              {range ? "" : ` (${w.long.toLowerCase()})`}
              {clearedAt !== null ? " since you cleared" : ""}.
            </p>
            {wi < windows.length - 1 && (
              <div className="space-y-2">
                <p className="text-[12px] text-muted-foreground">Expand to</p>
                <div className="flex justify-center gap-2">
                  {windows.slice(wi + 1, wi + 4).map((o) => (
                    <Button key={o.s} variant="outline" size="sm" onClick={() => setWi(windows.indexOf(o))}>
                      {o.long}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <ul className="divide-y">
            {headers && (
              <li className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-4 sm:grid-cols-[300px_minmax(0,1fr)] bg-muted/30 px-4 py-2 text-[11px] font-medium text-muted-foreground">
                <span className="flex items-center gap-2.5 pl-[18px]">
                  Room
                  <span className="ml-auto">Length</span>
                  <span>People</span>
                </span>
                <span>Timeline</span>
              </li>
            )}
            {shown.map((r) => {
              const isLive = r.state === "Observed active"
              const o = roomOrigin(r.room)
              const Kind = o.kind === "phone-in" || o.kind === "phone-out" ? Phone : Robot
              return (
                <li key={r.key}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button type="button" onClick={() => open(r.room)} className="grid w-full grid-cols-[minmax(0,150px)_minmax(0,1fr)] items-center sm:grid-cols-[300px_minmax(0,1fr)] gap-4 px-4 py-2.5 text-left hover:bg-muted/50">
                        <span className="flex min-w-0 items-center gap-2.5">
                          <span className={cn("size-2 shrink-0 rounded-full", isLive ? "animate-pulse bg-primary" : "bg-muted-foreground/40")} aria-label={r.state} />
                          <Kind size={14} className="shrink-0 text-muted-foreground" />
                          <span className="truncate font-mono text-[12px]">{r.room.name}</span>
                          <span className={cn("ml-auto shrink-0 text-[11px] tnum", isLive ? "text-foreground" : "text-muted-foreground")}>{length(Math.min(r.end, now) - r.start)}</span>
                          <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground tnum">
                            <Users size={12} />
                            {r.room.participants.length}
                          </span>
                        </span>
                        <span className="relative h-6">
                          <span className="absolute inset-0 rounded bg-muted/40" />
                          <span className={cn("absolute inset-y-1 rounded-sm", isLive ? "bg-primary/70" : "bg-muted-foreground/35")} style={{ left: `${x(r.start)}%`, width: `${Math.max(x(Math.min(r.end, now)) - x(r.start), 0.3)}%` }} />
                        </span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" align="start" className="text-xs">{`${r.state} · Last observed ${new Date(r.room.lastSeen * 1000).toLocaleString()} · ${length(r.room.lastSeen - r.room.createdMs / 1000)}`}</TooltipContent>
                  </Tooltip>
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <RoomDrawer room={openRoom} ms={ms} open={openRoom !== null} onOpenChange={(o) => !o && setOpenRoom(null)} />
    </div>
  )
}
