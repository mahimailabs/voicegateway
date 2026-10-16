import { JOIN_MS } from "@/lib/thresholds"
import { ArrowRight, Door, Headset, LockSimple, LockSimpleOpen, Microphone, Phone, Question, Robot, SignOut, WarningCircle } from "@phosphor-icons/react"
import { Link } from "@tanstack/react-router"
import { AudioPlayerButton, AudioPlayerProgress, AudioPlayerProvider, AudioPlayerTime } from "@/components/elevenlabs/audio-player"
import type { Icon } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import raw from "@/mocks/media-server.json"
import { useEmbedded } from "@/lib/url-state"
import { listCalls } from "@/lib/api"

// One room, opened from the Media server timeline: how it came together, what the agent was doing,
// who was in it with which tracks, and the room's own settings. Everything comes from RoomService and
// AgentDispatchService reads; fields a watch did not keep say so instead of showing a default.

// The fixture's shape. Watch 1 kept no track, permission or dispatch detail, so those are null there.
type Track = { sid: string; name: string; type: string; source: string; mime: string | null; versionMs: number; muted: boolean; stereo: boolean; dtx: boolean; red: boolean; encryption: string; features: string[] }
export type Participant = {
  sid: string
  kind: string
  region: string | null
  joinedAtMs: number
  agentName: string | null
  firstSeen: number
  lastSeen: number
  states: { t: number; state: string }[]
  permission: { canPublish: boolean; canSubscribe: boolean; canPublishData: boolean; hidden: boolean; agent: boolean } | null
  tracks: Track[] | null
  audioTracks: number
  sip?: { trunkId: string | null; ruleId: string | null; inviteMs: number | null; trunkNumber?: string | null; twilio?: boolean; statuses: { t: number; status: string }[] }
}
type Dispatch = { id: string; agentName: string | null; createdAtMs: number; jobs: { id: string; status: string; statuses: { t: number; status: string }[]; startedAtMs: number; workerId: string | null; agentId: string | null; recording: boolean }[] }
type RoomData = {
  sid: string
  name: string
  createdMs: number
  firstSeen: number
  lastSeen: number
  settings: { emptyTimeout: number | null; departureTimeout: number | null; maxParticipants: number; codecs: string[] | null; egressRecording: boolean | null }
  dispatches: Dispatch[] | null
  participants: Participant[]
  recording: { callRoom: string | null; url: string; durationS: number; startedAt: string } | null
}
export type MediaServerData = {
  source: string
  server: { edition: string; version: string; protocol: number; region: string; nodeId: string; joinMs: number; url: string }
  windows: { start: number; end: number; everySeconds: number; detail: boolean }[]
  sipTrunks: Record<string, { name: string; mediaEncryption: string; krispEnabled: boolean; source: string }>
  rooms: RoomData[]
}
export const data = raw as unknown as MediaServerData
export type Room = RoomData & { n: number }

export const regionCity: Record<string, string> = { oashburn1b: "Ashburn", osanjose1a: "San Jose", otoronto1a: "Toronto" }
export const kindIcon: Record<string, Icon> = { agent: Robot, sip: Phone, standard: Headset }
export const kindName: Record<string, string> = { agent: "Agent", sip: "Phone caller", standard: "Caller" }

// Where a room came from, as far as the reads show it. A phone caller names the dispatch rule that
// created the room; a dispatch without one could have come from the API or a rule, which LiveKit does
// not distinguish; the earliest watch kept no dispatch detail.
export type Origin = { kind: "phone-in" | "phone-out" | "dispatch" | "unknown"; label: string; note: string; ruleId?: string }
export function roomOrigin(r: Room): Origin {
  const sip = r.participants.find((p) => p.kind === "sip")?.sip
  if (sip?.ruleId) return { kind: "phone-in", label: "Phone call", note: `Inbound call through trunk ${sip.trunkId ?? "unknown"}, placed in this room by dispatch rule ${sip.ruleId}.`, ruleId: sip.ruleId }
  if (sip) return { kind: "phone-out", label: "Outbound call", note: `A SIP participant with no dispatch rule: an outbound call through trunk ${sip.trunkId ?? "unknown"}.` }
  const d = r.dispatches?.find((x) => x.agentName)
  if (d) return { kind: "dispatch", label: "Agent dispatch", note: `ListDispatch shows ${d.agentName} dispatched here. Whether the API or a dispatch rule created it is not reported.` }
  return { kind: "unknown", label: "Origin unknown", note: r.dispatches === null ? "This watch kept no dispatch detail." : "No dispatch and no phone caller seen: a client joined directly." }
}

// A room still listed at the last read of its watch was open when we stopped watching; its end is unknown.
export const openAtWatchEnd = (r: { lastSeen: number }) => data.windows.some((w) => r.lastSeen >= w.end - 1 && r.lastSeen <= w.end + 1)

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
const plus = (ms: number) => `${ms < 0 ? "-" : "+"}${(Math.abs(ms) / 1000).toFixed(2)} s`
const secs = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${s.toFixed(1)} s`)
const codecName: Record<string, string> = { "audio/red": "Opus with RED", "audio/opus": "Opus", "audio/PCMU": "G.711 μ-law", "audio/PCMA": "G.711 A-law" }
const who = (p: Participant) => `${kindName[p.kind] ?? p.kind}${p.agentName ? ` ${p.agentName}` : ""}`
const stateTone: Record<string, string> = { speaking: "bg-primary", thinking: "bg-primary/40", listening: "bg-muted-foreground/25" }

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold">{title}</h3>
        {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-[13px]">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  )
}

export function RoomDrawer({ room, open, onOpenChange }: { room: Room | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" onOpenAutoFocus={(e) => e.preventDefault()} className="gap-0 overflow-y-auto p-0 data-[side=right]:w-[560px] data-[side=right]:sm:max-w-none">
        {room && <RoomBody room={room} />}
      </SheetContent>
    </Sheet>
  )
}

const originIcon: Record<Origin["kind"], Icon> = { "phone-in": Phone, "phone-out": Phone, dispatch: Robot, unknown: Question }
function OriginLine({ room }: { room: Room }) {
  const embedded = useEmbedded()
  const o = roomOrigin(room)
  const I = originIcon[o.kind]
  return (
    <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
      <I size={14} />
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help underline decoration-dotted underline-offset-4">{o.label}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-72 text-xs">{o.note}</TooltipContent>
      </Tooltip>
      {o.ruleId &&
        (embedded ? (
          <span>rule {o.ruleId}</span>
        ) : (
          <Link to="/sip" className="text-primary hover:underline">
            rule {o.ruleId}
          </Link>
        ))}
    </p>
  )
}

export function RoomBody({ room }: { room: Room }) {
  const t0 = room.createdMs
  const stillOpen = openAtWatchEnd(room)
  const caller = room.participants.find((p) => p.kind !== "agent")
  const agent = room.participants.find((p) => p.kind === "agent")
  const wait = caller && agent ? agent.joinedAtMs - caller.joinedAtMs : null
  const busiest = room.participants.filter((p) => p.kind === "agent").sort((a, b) => b.states.length - a.states.length)[0]
  const detail = room.dispatches !== null
  const dispatches = room.dispatches?.filter((d) => d.agentName || d.jobs.length) ?? []
  const tracks = room.participants.flatMap((p) => p.tracks ?? [])
  const sipCaller = room.participants.find((p) => p.kind === "sip")
  const sip = Boolean(sipCaller)
  const trunk = sipCaller?.sip?.trunkId ? data.sipTrunks[sipCaller.sip.trunkId] : undefined
  const media = trunk ? ({ SIP_MEDIA_ENCRYPT_DISABLE: "RTP", SIP_MEDIA_ENCRYPT_ALLOW: "SRTP if offered", SIP_MEDIA_ENCRYPT_REQUIRE: "SRTP" } as Record<string, string>)[trunk.mediaEncryption] : undefined
  const e2ee = detail ? tracks.length > 0 && tracks.every((t) => t.encryption !== "NONE") : null

  // The sequence LiveKit recorded, measured from the room's creation.
  const every = (data.windows.find((w) => room.lastSeen >= w.start - 1 && room.lastSeen <= w.end + 1)?.everySeconds ?? 2) * 1000
  const steps: { at: number; icon: Icon; label: string; note?: string; alert?: boolean }[] = [
    ...(sipCaller?.sip?.inviteMs ? [{ at: sipCaller.sip.inviteMs, icon: Phone, label: "Call came in", note: "SIP INVITE time, from the caller's lk.sip.inviteTime attribute." }] : []),
    ...(sipCaller?.sip?.statuses ?? []).map((x) => ({ at: x.t * 1000, icon: Phone, label: `Call ${x.status === "active" ? "answered" : x.status}`, note: `sip.callStatus became ${x.status} by this read.` })),
    ...dispatches.map((d) => ({ at: d.createdAtMs, icon: Robot, label: "Agent dispatched", note: d.jobs[0] ? `${d.agentName}, job ${d.jobs[0].id} on worker ${d.jobs[0].workerId ?? "unknown"}` : undefined })),
    ...room.participants.map((p, k) => ({
      at: p.joinedAtMs,
      icon: kindIcon[p.kind] ?? Headset,
      label: `${kindName[p.kind] ?? p.kind} joined${room.participants.slice(0, k).some((q) => q.kind === p.kind) ? " again" : ""}`,
    })),
    // Someone gone before the room's last read left between two reads.
    ...room.participants
      .filter((p) => p.lastSeen < room.lastSeen - 1)
      .map((p) => ({ at: p.lastSeen * 1000, icon: SignOut, label: `${kindName[p.kind] ?? p.kind} left`, note: `Last listed at this time; gone by the next read ${every / 1000} s later.`, alert: p.kind === "agent" })),
    ...dispatches.flatMap((d) =>
      d.jobs.flatMap((j) =>
        j.statuses
          .filter((x) => x.status === "JS_FAILED")
          .map((x) => ({ at: x.t * 1000, icon: WarningCircle, label: "Agent job reported failed", note: `ListDispatch reported ${j.id} as failed. Its reason is not in the API; the worker's own logs have it.`, alert: true })),
      ),
    ),
    ...room.participants.flatMap((p) => (p.tracks ?? []).map((t) => ({ at: t.versionMs, icon: Microphone, label: `${kindName[p.kind] ?? p.kind} mic on`, note: "Track version time: the server's last change to this track, which here was its publish." }))),
  ].sort((a, b) => a.at - b.at)

  // How the audio is protected on each hop. Signalling and WebRTC media are encrypted by protocol;
  // end-to-end comes from the tracks; the phone leg needs the SIP trunk's settings.
  const security: { icon: Icon; label: string; value: string; ok: boolean | null; note: string }[] = [
    { icon: LockSimple, label: "Signalling", value: data.server.url.startsWith("wss://") ? "TLS" : "Plain", ok: data.server.url.startsWith("wss://"), note: `From the project URL, ${data.server.url.split("://")[0]}://.` },
    { icon: LockSimple, label: "Media to LiveKit", value: "DTLS-SRTP", ok: true, note: "Inferred: WebRTC always encrypts media between each participant and the media server." },
    {
      icon: e2ee === null ? Question : e2ee ? LockSimple : LockSimpleOpen,
      label: "End to end",
      value: e2ee === null ? "Unknown" : e2ee ? "On" : "Off",
      ok: e2ee,
      note: e2ee === null ? "Track encryption was not recorded in this watch." : e2ee ? "Every track is encrypted end to end." : "Tracks report no end-to-end encryption, so the media server can hear the audio.",
    },
    ...(sip
      ? [
          {
            icon: media === "RTP" ? LockSimpleOpen : media ? LockSimple : Question,
            label: "Phone leg",
            value: media ?? "Unknown",
            ok: media ? media !== "RTP" : null,
            note: trunk
              ? `From the media encryption setting on trunk ${sipCaller?.sip?.trunkId} (${trunk.source}) ${media === "RTP" ? "Audio between the phone side and LiveKit is not encrypted." : ""} The transport of an inbound call (UDP, TCP or TLS) is not in the API.`
              : "Set on the SIP trunk, found through the caller's sip.trunkID; this trunk was not read.",
          },
        ]
      : []),
  ]

  return (
    <div className="space-y-7 p-6">
      <SheetHeader className="space-y-1 p-0">
        <div className="flex items-center gap-2">
          <SheetTitle className="text-[22px] font-semibold tracking-[-0.01em]">Room {room.n}</SheetTitle>
          <Badge variant="outline" className="font-normal">{stillOpen ? "Open when we stopped" : "Closed"}</Badge>
        </div>
        <SheetDescription className="truncate font-mono text-xs">{room.name}</SheetDescription>
        <OriginLine room={room} />
      </SheetHeader>

      <div className="grid grid-cols-3 gap-4">
        {[
          [`${secs(room.lastSeen - t0 / 1000)}${stillOpen ? "+" : ""}`, "length"],
          [String(room.participants.length), "people"],
          [wait === null ? "Unknown" : wait >= 0 ? secs(wait / 1000) : "first", "agent after caller"],
        ].map(([v, l]) => (
          <div key={l}>
            <p className={cn("text-[24px] leading-none font-semibold tnum", l.startsWith("agent") && wait !== null && wait > JOIN_MS && "text-destructive")}>{v}</p>
            <p className="mt-1.5 text-[12px] text-muted-foreground">{l}</p>
          </div>
        ))}
      </div>

      <div className={cn("grid gap-2", security.length > 3 ? "grid-cols-4" : "grid-cols-3")}>
        {security.map((s) => {
          const I = s.icon
          return (
            <Tooltip key={s.label}>
              <TooltipTrigger asChild>
                <div className="cursor-help rounded-xl border p-3">
                  <I size={16} weight="bold" className={cn(s.ok === true && "text-primary", s.ok === false && "text-destructive", s.ok === null && "text-muted-foreground")} />
                  <p className="mt-2 text-[14px] font-medium">{s.value}</p>
                  <p className="text-[12px] text-muted-foreground">{s.label}</p>
                </div>
              </TooltipTrigger>
              <TooltipContent className="max-w-64 text-xs">{s.note}</TooltipContent>
            </Tooltip>
          )
        })}
      </div>

      <Section title="Timeline" hint={`from ${clock(t0)}`}>
        <ol className="space-y-2">
          {[{ at: t0, icon: Door, label: "Room created", note: undefined as string | undefined, alert: false }, ...steps].sort((a, b) => a.at - b.at).map((s, k) => {
            const I = s.icon
            const line = (
              <span className="flex items-center gap-2 text-[13px]">
                <span className="w-16 text-muted-foreground tnum">{plus(s.at - t0)}</span>
                <I size={14} className={"alert" in s && s.alert ? "text-destructive" : "text-muted-foreground"} />
                <span className={cn("alert" in s && s.alert && "text-destructive")}>{s.label}</span>
              </span>
            )
            return (
              <li key={k}>
                {s.note ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="cursor-help">{line}</span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-72 text-xs">{s.note}</TooltipContent>
                  </Tooltip>
                ) : (
                  line
                )}
              </li>
            )
          })}
        </ol>
      </Section>

      {busiest && new Set(busiest.states.map((s) => s.state)).size > 1 && <AgentStates agent={busiest} />}

      <Section title="People">
        <ul className="divide-y rounded-xl border">
          {room.participants.map((p) => {
            const I = kindIcon[p.kind] ?? Headset
            const flags = (p.tracks ?? []).flatMap((t) => [codecName[t.mime ?? ""] ?? t.mime ?? "codec unknown", t.stereo ? "stereo" : "mono", ...(t.dtx ? ["DTX"] : []), ...(t.muted ? ["muted"] : []), ...t.features])
            return (
              <li key={p.sid} className="flex items-center gap-3 px-4 py-3">
                <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full", p.kind === "agent" ? "bg-primary text-primary-foreground" : "bg-muted")}>
                  <I size={14} weight="bold" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-medium">{who(p)}</p>
                  <p className="text-[12px] text-muted-foreground">
                    {regionCity[p.region ?? ""] ?? p.region ?? "Region unknown"} · {clock(p.joinedAtMs)}
                  </p>
                </div>
                <div className="flex flex-wrap justify-end gap-1">
                  {flags.map((x) => (
                    <span key={x} className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{x}</span>
                  ))}
                </div>
              </li>
            )
          })}
        </ul>
      </Section>

      {detail && <Recording room={room} requested={dispatches.some((d) => d.jobs.some((j) => j.recording))} />}

      {detail ? (
        <Section title="Room">
          <div className="divide-y rounded-xl border px-4">
            {dispatches.map((d) => (
              <Row key={d.id} k="Agent" v={<span>{d.agentName ?? "Unnamed"}{d.jobs[0] && <span className="ml-2 font-mono text-[11px] text-muted-foreground">{d.jobs[0].workerId}</span>}</span>} />
            ))}
            <Row k="Closes" v={`${room.settings.emptyTimeout ?? "?"} s empty, ${room.settings.departureTimeout ?? "?"} s after last leaves`} />
            <Row k="Max people" v={room.settings.maxParticipants || "No limit"} />
            <Row
              k="Audio codecs"
              v={(room.settings.codecs ?? [])
                .filter((c) => c.startsWith("audio/"))
                .map((c) => c.slice(6))
                .join(", ")}
            />
          </div>
        </Section>
      ) : (
        <p className="text-[12px] text-muted-foreground">Tracks, room settings and dispatch were not recorded for rooms in the earlier watch.</p>
      )}
    </div>
  )
}

// The agent's own stereo recording, uploaded at session end (decisions 0014, 0015), and LiveKit egress.
function Recording({ room, requested }: { room: Room; requested: boolean }) {
  const embedded = useEmbedded()
  const rec = room.recording
  const offset = rec ? new Date(rec.startedAt).getTime() - room.createdMs : null
  return (
    <Section title="Recording">
      <div className="divide-y rounded-xl border px-4">
        <div className="py-3">
          <div className="flex items-center justify-between gap-4 text-[13px]">
            <span className="text-muted-foreground">Agent recording</span>
            {rec && !rec.callRoom ? (
              <span className="text-muted-foreground">Not in Calls</span>
            ) : rec?.callRoom && embedded ? (
              <span>In Calls</span>
            ) : rec?.callRoom ? (
              <Link to="/calls/$callId" params={{ callId: listCalls().find((c) => c.room === rec.callRoom)?.id ?? rec.callRoom }} className="flex items-center gap-1 text-primary hover:underline">
                Open call <ArrowRight size={12} />
              </Link>
            ) : (
              <span>{requested ? "Requested, not received" : "Not requested"}</span>
            )}
          </div>
          {rec && (
            <AudioPlayerProvider>
              <div className="mt-2 flex items-center gap-3">
                <AudioPlayerButton item={{ id: room.sid, src: rec.url }} variant="default" size="icon-sm" className="rounded-full" />
                <AudioPlayerProgress className="flex-1" />
                <span className="text-[12px] text-muted-foreground tnum">
                  <AudioPlayerTime /> / {secs(rec.durationS)}
                </span>
              </div>
              {offset !== null && offset > 2000 && <p className="mt-2 text-[12px] text-muted-foreground">Starts at {plus(offset)}, when the agent session started. Stereo: caller left, agent right.</p>}
            </AudioPlayerProvider>
          )}
        </div>
        <Row k="Egress recording" v={room.settings.egressRecording ? "On" : "Off"} />
      </div>
    </Section>
  )
}

// What the agent said it was doing, from its lk.agent.state attribute at each read.
function AgentStates({ agent }: { agent: Participant }) {
  const start = agent.states[0].t
  const end = agent.lastSeen
  const span = Math.max(end - start, 1)
  const every = data.windows.find((w) => start >= w.start - 1 && start <= w.end + 1)?.everySeconds
  const parts = agent.states.map((s, k) => ({ state: s.state, from: s.t, to: agent.states[k + 1]?.t ?? end }))
  const total = (st: string) => parts.filter((p) => p.state === st).reduce((a, p) => a + (p.to - p.from), 0)
  return (
    <Section title="What the agent was doing" hint={every ? `read every ${every} s, shorter states missed` : undefined}>
      <div className="flex h-6 overflow-hidden rounded-md">
        {parts.map((p, k) => (
          <Tooltip key={k}>
            <TooltipTrigger asChild>
              <span className={cn("h-full border-r border-background last:border-r-0", stateTone[p.state] ?? "bg-muted")} style={{ width: `${((p.to - p.from) / span) * 100}%` }} />
            </TooltipTrigger>
            <TooltipContent>{`${p.state}, ${secs(p.to - p.from)} from ${clock(p.from * 1000)}`}</TooltipContent>
          </Tooltip>
        ))}
      </div>
      <div className="flex flex-wrap gap-4 text-[12px] text-muted-foreground">
        {["listening", "thinking", "speaking"].filter((st) => parts.some((p) => p.state === st)).map((st) => (
          <span key={st} className="flex items-center gap-1.5">
            <span className={cn("size-2.5 rounded-sm", stateTone[st])} />
            {st} <span className="text-foreground tnum">{secs(total(st))}</span>
          </span>
        ))}
      </div>
    </Section>
  )
}
