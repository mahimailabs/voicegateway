import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "@tanstack/react-router"
import { Archive, ArrowCounterClockwise, CaretRight, ChatsCircle, Clock, Desktop, Flask, Globe, Hash, Phone, PhoneCall, Tag, Timer, Waveform, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { TimeRangePicker, useUrlRange } from "@/components/time-range-picker"
import { type Columns, DataTable, type Field, matches } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { createFilterQuery } from "@/components/reui/filters/filters-query"
import type { FilterQuery } from "@/components/reui/filters/filters-types"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toast } from "sonner"
import { setArchived, useArchived } from "./call-archive"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { type Call, fromCapture, listCalls } from "@/lib/api"
import { CallMeta } from "@/features/call/call-meta"
import { CallTags, TagChip, useCallTags } from "@/features/call/call-tags"
import { CallView } from "@/features/call/call-view"
import { buildModel, fmtMs, pipelineLabel } from "@/features/call/call-model"
import { BUDGET_MS, findingsFor } from "@/features/call/call-steps"
import { data as rooms } from "@/features/media-server/room-drawer"
import { useUrlState } from "@/lib/url-state"

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}
// Archive and row key: the room SID as before; the contract id when the room reported none.
const keyOf = (c: Call) => c.roomSid ?? c.id
const agentTurns = (c: Call) => (c.spans ? c.spans.filter((s) => s.name === "agent_turn").length : (c.turns?.length ?? null))

// Dev only: a design preview of the recording view, built from room 10's real trace plus synthetic speech
// generated locally into public/preview (gitignored). Never shipped, never presented as a captured call.
function usePreviewCall(): Call | null {
  const [call, setCall] = useState<Call | null>(null)
  useEffect(() => {
    if (!import.meta.env.DEV) return
    fetch("/preview/recording.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((rec) => {
        const base = listCalls().find((c) => c.room === "spike-room-10")
        if (!rec || !base) return
        // Re-adapted without the base's id and clocks so the preview gets its own id and a recording clock.
        setCall(
          fromCapture({
            ...base,
            id: undefined,
            clocks: undefined,
            room: "design-preview",
            roomSid: "PREVIEW",
            recording: { url: "/preview/recording.wav", duration: rec.duration, segments: rec.segments, peaks: rec.peaks, preview: "Design preview: room 10's real trace with synthetic speech made from its transcript. Not a captured call." },
          }),
        )
      })
      .catch(() => setCall(null))
  }, [])
  return call
}

// How each call reached the agent. Rooms we watched say it from their participants; console and
// local test sessions say it from where they were captured.
type Channel = "phone" | "web" | "console" | "local"
const channelInfo: Record<Channel, { icon: Icon; label: string }> = {
  phone: { icon: Phone, label: "Phone" },
  web: { icon: Globe, label: "Web or app" },
  console: { icon: Desktop, label: "Agent console" },
  local: { icon: Flask, label: "Local test" },
}
function channelOf(c: Call): Channel {
  if (c.recording?.preview) return "local"
  const r = rooms.rooms.find((x) => x.sid === c.roomSid)
  if (r?.participants.some((p) => p.kind === "sip")) return "phone"
  if (r) return "web"
  if (c.room.startsWith("console")) return "console"
  if (c.room.startsWith("vg-sip")) return "phone"
  return c.room.startsWith("spike") ? "local" : "web"
}

export type Row = { call: Call; channel: Channel; answers: number[]; p50: number | null; issues: { high: number; medium: number; titles: string[] }; started: number; label: string }
export function rowOf(c: Call): Row {
  const model = buildModel(c)
  const answers = model.turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
  const f = findingsFor(c, model)
  return {
    call: c,
    channel: channelOf(c),
    answers,
    p50: median(answers),
    issues: { high: f.filter((x) => x.severity === "high").length, medium: f.filter((x) => x.severity === "medium").length, titles: f.filter((x) => x.severity !== "info").map((x) => x.title) },
    started: Date.parse(c.startedAt) / 1000,
    label: [model.pipeline !== "unknown" ? pipelineLabel[model.pipeline] : null, model.models.slice(0, 2).join(" · ") || null].filter(Boolean).join(" · "),
  }
}

function StatCard({ icon: I, value, label, children, alert }: { icon: Icon; value: React.ReactNode; label: string; children?: React.ReactNode; alert?: boolean }) {
  return (
    <div className="rounded-2xl border bg-background p-4">
      <div className="flex items-center justify-between">
        <p className="text-[13px] text-muted-foreground">{label}</p>
        <I size={18} className={alert ? "text-destructive" : "text-muted-foreground"} />
      </div>
      <p className={cn("mt-2 text-[28px] leading-none font-semibold tracking-[-0.02em] tnum", alert && "text-destructive")}>{value}</p>
      {children && <div className="mt-3">{children}</div>}
    </div>
  )
}

// Every turn's answer time on one 0 to 5 s scale, red past the response budget.
const SCALE = 5000
function AnswerDots({ answers }: { answers: number[] }) {
  return (
    <div className="relative h-4 w-28">
      <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
      <span className="absolute top-0 bottom-0 w-px bg-muted-foreground/40" style={{ left: `${(BUDGET_MS / SCALE) * 100}%` }} />
      {answers.map((a, k) => (
        <span key={k} className={cn("absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full", a > BUDGET_MS ? "bg-destructive" : "bg-primary")} style={{ left: `${Math.min(a / SCALE, 1) * 100}%` }} />
      ))}
    </div>
  )
}

// react-resizable-panels handles the pointer at the document level, so a press on a pane handle inside
// the drawer never reaches React and Radix reads it as a press outside. Ignore presses that are inside.
const keepOpenOnInsidePress = (e: { detail: { originalEvent: PointerEvent }; preventDefault: () => void }) => {
  // A toast's Undo is outside the drawer too; clicking it must not close the drawer.
  if ((e.detail.originalEvent.target as Element | null)?.closest?.('[data-slot="sheet-content"], [data-sonner-toaster]')) e.preventDefault()
}

// The drawer's format, so a call reads the same number in both places.
const fmt = fmtMs

const length = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s} s`)
const right = { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" }

export function columns(tagsOf: (room: string) => string[]): Columns<Row> {
  return [
    {
      id: "room",
      accessorFn: (r) => r.call.room,
      header: ({ column }) => <DataGridColumnHeader title="Call" column={column} />,
      size: 300,
      cell: ({ row: { original: r } }) => (
        <div className="min-w-0 py-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-mono text-[12px]">{r.call.room}</span>
            {r.call.recording && !r.call.recording.preview && <Waveform size={14} className="shrink-0 text-primary" aria-label="Recorded" />}
            {r.call.recording?.preview && <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] text-accent-foreground">Design preview</span>}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">{r.label || "pipeline not reported"}</div>
          {tagsOf(r.call.room).length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {tagsOf(r.call.room).map((t) => (
                <TagChip key={t} tag={t} />
              ))}
            </div>
          )}
        </div>
      ),
    },
    {
      id: "channel",
      accessorFn: (r) => channelInfo[r.channel].label,
      header: ({ column }) => <DataGridColumnHeader title="Channel" column={column} />,
      size: 140,
      cell: ({ row: { original: r } }) => {
        const ch = channelInfo[r.channel]
        return (
          <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <ch.icon size={14} /> {ch.label}
          </span>
        )
      },
    },
    {
      id: "started",
      accessorFn: (r) => r.started,
      header: ({ column }) => <DataGridColumnHeader title="Started" column={column} />,
      size: 130,
      cell: ({ row: { original: r } }) => <span className="text-[12px] text-muted-foreground tnum">{new Date(r.call.startedAt).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>,
    },
    {
      id: "length",
      // Unknown sorts below every known length instead of reading as zero.
      accessorFn: (r) => r.call.durationSeconds ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Length" column={column} />,
      size: 90,
      meta: right,
      cell: ({ row: { original: r } }) => <span className="text-[13px] tnum">{r.call.durationSeconds !== null ? length(r.call.durationSeconds) : <span className="text-muted-foreground">?</span>}</span>,
    },
    {
      id: "turns",
      // Unknown sorts below every known count instead of reading as zero.
      accessorFn: (r) => agentTurns(r.call) ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Turns" column={column} />,
      size: 80,
      meta: right,
      cell: ({ row: { original: r } }) => <span className="text-[13px] tnum">{agentTurns(r.call) ?? <span className="text-muted-foreground">?</span>}</span>,
    },
    {
      id: "answer",
      accessorFn: (r) => r.p50 ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Answer time" column={column} />,
      size: 190,
      cell: ({ row: { original: r } }) =>
        r.answers.length ? (
          <div className="flex items-center gap-2">
            <span className={cn("w-12 text-[13px] tnum", r.p50 !== null && r.p50 > BUDGET_MS && "text-destructive")}>{fmt(r.p50!)}</span>
            <AnswerDots answers={r.answers} />
          </div>
        ) : (
          <span className="text-[12px] text-muted-foreground">not measured</span>
        ),
    },
    {
      id: "issues",
      accessorFn: (r) => r.issues.high * 100 + r.issues.medium,
      header: ({ column }) => <DataGridColumnHeader title="Issues" column={column} />,
      size: 150,
      cell: ({ row: { original: r } }) =>
        r.issues.high + r.issues.medium ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex cursor-help items-center gap-1">
                {r.issues.high > 0 && <span className="rounded-md bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{r.issues.high} high</span>}
                {r.issues.medium > 0 && <span className="rounded-md bg-amber-500/10 px-1.5 text-[11px] text-amber-700 tnum dark:text-amber-400">{r.issues.medium} medium</span>}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 text-xs">
              <ul className="space-y-0.5">
                {r.issues.titles.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-[12px] text-muted-foreground">none</span>
        ),
    },
    { id: "go", header: () => null, size: 32, enableSorting: false, cell: () => <CaretRight className="text-muted-foreground" /> },
  ]
}

// Unknown values (no answer measured, turns not reported) match only "is empty", never a number.
function callFields(allTags: string[], tagsOf: (room: string) => string[]): Field<Row>[] {
  return [
    { id: "room", label: "Room", type: "text", icon: <Hash />, get: (r) => r.call.room },
    { id: "channel", label: "Channel", type: "select", icon: <Phone />, options: (Object.keys(channelInfo) as Channel[]).map((c) => ({ value: c, label: channelInfo[c].label })), get: (r) => r.channel },
    {
      id: "issues",
      label: "Issues",
      type: "select",
      icon: <WarningCircle />,
      options: [
        { value: "high", label: "High" },
        { value: "medium", label: "Medium" },
        { value: "none", label: "None" },
      ],
      get: (r) => (r.issues.high ? "high" : r.issues.medium ? "medium" : "none"),
    },
    { id: "answer", label: "Answer time (ms)", type: "number", icon: <Timer />, get: (r) => r.p50 },
    { id: "length", label: "Length (s)", type: "number", icon: <Clock />, get: (r) => r.call.durationSeconds },
    { id: "turns", label: "Turns", type: "number", icon: <ChatsCircle />, get: (r) => agentTurns(r.call) },
    { id: "recorded", label: "Recorded", type: "boolean", icon: <Waveform />, get: (r) => Boolean(r.call.recording && !r.call.recording.preview) },
    ...(allTags.length ? [{ id: "tag", label: "Tag", type: "multiselect" as const, icon: <Tag />, options: allTags.map((t) => ({ value: t, label: t })), get: (r: Row) => tagsOf(r.call.room) }] : []),
  ]
}

export function CallsPage() {
  const preview = usePreviewCall()
  // The open call lives in the URL, /calls/<room>, so a call has a link to share.
  const { room } = useParams({ strict: false }) as { room?: string }
  const navigate = useNavigate()
  const [query, setQuery] = useUrlState<FilterQuery>(
    "q",
    useMemo(() => createFilterQuery([]), []),
  )
  const tags = useCallTags()
  const all = useMemo(() => (preview ? [preview, ...listCalls()] : listCalls()).map(rowOf).sort((a, b) => b.started - a.started), [preview])
  const open = room ? (all.find((r) => r.call.room === room)?.call ?? null) : null
  // Opening or closing a call keeps the list's filters and range; closing drops the drawer's own state.
  const setOpen = (c: Call | null) =>
    c
      ? navigate({ to: "/calls/$room", params: { room: c.room }, search: (p: Record<string, unknown>) => p } as never)
      : navigate({ to: "/calls", search: ({ turn: _t, pane: _p, events: _e, ...rest }: Record<string, unknown>) => rest } as never)
  // The range ends at the latest end time; a call still open counts from its start.
  const ends = all.map((r) => (r.call.endedAt !== null ? Date.parse(r.call.endedAt) / 1000 : r.started))
  const now = ends.length > 0 ? Math.max(...ends) : Date.now() / 1000
  const [range, setRange] = useUrlRange("7d", now)
  const fields = useMemo(() => callFields(tags.all, tags.of), [tags])
  // The stat cards and the table read the same rows, so a filter changes both.
  const archived = useArchived()
  const [view, setView] = useUrlState<"active" | "archived">("view", "active")
  const isArchived = (r: { call: Call }) => archived.includes(keyOf(r.call))
  const archivedCount = all.filter(isArchived).length
  // The stat cards and the table read the same rows, so a filter or the archive tab changes both.
  const rows = all.filter((r) => (view === "archived") === isArchived(r) && r.started >= range.from && r.started <= range.to && matches(r, query, fields))
  const archive = (list: Call[], to: boolean, done?: () => void) => {
    const before = archived
    const ids = list.map(keyOf)
    setArchived(to ? [...new Set([...archived, ...ids])] : archived.filter((x) => !ids.includes(x)))
    done?.()
    const what = list.length === 1 ? list[0].room : `${list.length} calls`
    toast(to ? `Archived ${what}` : `Moved ${what} back to calls`, { action: { label: "Undo", onClick: () => setArchived(before) } })
  }
  // The dev-only design preview is listed but never counted.
  const counted = rows.filter((r) => !r.call.recording?.preview)
  const pooled = counted.flatMap((r) => r.answers)
  const p50 = median(pooled)
  const over = pooled.filter((a) => a > BUDGET_MS).length
  const flagged = counted.filter((r) => r.issues.high + r.issues.medium > 0).length
  const recorded = counted.filter((r) => r.call.recording).length
  const channels = (Object.keys(channelInfo) as Channel[]).filter((c) => all.some((r) => r.channel === c))

  return (
    <div className="mx-auto max-w-[1200px] space-y-8">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Calls</h1>
          <TimeRangePicker value={range} onChange={setRange} now={now} />
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <StatCard icon={PhoneCall} value={counted.length} label="Calls">
            <div className="flex gap-0.5">
              {channels.map((c) => {
                const n = counted.filter((r) => r.channel === c).length
                return n ? <span key={c} className="h-2 rounded-sm bg-primary/60" style={{ flex: n }} title={`${channelInfo[c].label}: ${n}`} /> : null
              })}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">{channels.map((c) => `${counted.filter((r) => r.channel === c).length} ${channelInfo[c].label.toLowerCase()}`).join(" · ")}</p>
          </StatCard>
          <StatCard icon={Timer} value={p50 === null ? "Unknown" : fmt(p50)} label="Median answer time" alert={p50 !== null && p50 > BUDGET_MS}>
            <AnswerDots answers={pooled} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{`${over} of ${pooled.length} turns over the ${BUDGET_MS / 1000} s budget`}</p>
          </StatCard>
          <StatCard icon={WarningCircle} value={`${flagged} of ${counted.length}`} label="Calls with issues" alert={flagged > 0}>
            <div className="flex gap-0.5">
              {counted.map((r) => (
                <span key={keyOf(r.call)} className={cn("h-2 flex-1 rounded-sm", r.issues.high ? "bg-destructive" : r.issues.medium ? "bg-amber-500" : "bg-primary/40")} />
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">one bar per call, red has a high issue</p>
          </StatCard>
          <StatCard icon={Waveform} value={`${recorded} of ${counted.length}`} label="Recorded">
            <p className="text-[11px] text-muted-foreground">from the agent's own upload, opt-in per project</p>
          </StatCard>
        </div>
      </header>

      <section className="space-y-3">
        <Tabs value={view} onValueChange={(v) => setView(v as "active" | "archived")}>
          <TabsList>
            <TabsTrigger value="active">
              Calls <span className="ml-1 text-[11px] text-muted-foreground tnum">{all.length - archivedCount}</span>
            </TabsTrigger>
            <TabsTrigger value="archived">
              <Archive /> Archived <span className="ml-1 text-[11px] text-muted-foreground tnum">{archivedCount}</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <DataTable
          key={view}
          data={rows}
          columns={columns(tags.of)}
          fields={fields}
          query={query}
          onQueryChange={setQuery}
          getRowId={(r) => keyOf(r.call)}
          onRowClick={(r) => setOpen(r.call)}
          sort={[{ id: "started", desc: true }]}
          empty={view === "archived" ? "No archived calls." : "No calls match these filters."}
          bulk={(sel, clear) =>
            view === "active" ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  archive(
                    sel.map((r) => r.call),
                    true,
                    clear,
                  )
                }
              >
                <Archive /> Archive
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  archive(
                    sel.map((r) => r.call),
                    false,
                    clear,
                  )
                }
              >
                <ArrowCounterClockwise /> Move back to calls
              </Button>
            )
          }
        />
        <p className="text-[12px] text-muted-foreground">{`Answer time: caller stops talking to agent audio, per turn. The line marks the ${BUDGET_MS / 1000} s budget, a setting, not a measurement.`}</p>
      </section>
      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent side="right" onOpenAutoFocus={(e) => e.preventDefault()} onPointerDownOutside={keepOpenOnInsidePress} className="gap-0 p-0 data-[side=right]:w-[80vw] data-[side=right]:sm:max-w-none">
          {open && (
            <>
              <SheetHeader className="border-b px-5 py-4 pr-14">
                <SheetTitle className="text-lg">{open.room}</SheetTitle>
                <SheetDescription asChild>
                  <div>
                    <CallMeta call={open} />
                  </div>
                </SheetDescription>
                <div className="flex items-center gap-2 pt-1.5">
                  <CallTags room={open.room} />
                  <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground" onClick={() => archive([open], !archived.includes(keyOf(open)))}>
                    {archived.includes(keyOf(open)) ? <ArrowCounterClockwise /> : <Archive />}
                    {archived.includes(keyOf(open)) ? "Unarchive" : "Archive"}
                  </Button>
                </div>
              </SheetHeader>
              <div className="min-h-0 flex-1">
                <CallView key={keyOf(open)} call={open} />
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
