import { useEffect, useMemo } from "react"
import { useNavigate, useParams } from "@tanstack/react-router"
import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import type { SortingState } from "@tanstack/react-table"
import { Archive, ArrowCounterClockwise, CaretRight, ChatsCircle, Clock, Desktop, Flask, Globe, Hash, Phone, PhoneCall, Question, Tag, Timer, Waveform, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { TimeRangePicker, useUrlRange } from "@/components/time-range-picker"
import { type Columns, DataTable, type Field } from "@/components/data-table"
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
import { ApiError, type Call, type CallSummary, type CallTotals, callCountQuery, callPagesQuery, callQuery, latestCallQuery } from "@/lib/api"
import { serverOperators, toServerParams } from "./call-filters"
import { usePreviewCall } from "@/mocks/preview"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Spinner } from "@/components/ui/spinner"
import { CallMeta } from "@/features/call/call-meta"
import { CallTags, TagChip, useCallTags } from "@/features/call/call-tags"
import { CallView } from "@/features/call/call-view"
import { fmtMs, pipelineLabel } from "@/features/call/call-model"
import { BUDGET_MS } from "@/features/call/call-steps"
import { useUrlState } from "@/lib/url-state"

// Archive and row key: the room SID as before; the contract id when the room reported none.
const keyOf = (c: { roomSid: string | null; id: string }) => c.roomSid ?? c.id

// How each call reached the agent, as the server classifies it (CallSummary.channel).
type Channel = CallSummary["channel"]
const channelInfo: Record<Channel, { icon: Icon; label: string }> = {
  phone: { icon: Phone, label: "Phone" },
  web: { icon: Globe, label: "Web or app" },
  console: { icon: Desktop, label: "Agent console" },
  local: { icon: Flask, label: "Local test" },
  unknown: { icon: Question, label: "Unknown" },
}

// A row is the server's summary of a call. preview marks the dev-only design preview, which is
// built in the browser and never counted.
const DEFAULT_SORT: SortingState = [{ id: "started", desc: true }]
// The cards when the filters rule each other out: no call matches, which is known, not unknown.
const NO_CALLS: CallTotals = {
  callCount: 0,
  byChannel: { phone: 0, web: 0, console: 0, local: 0, unknown: 0 },
  answers: { turns: 0, overBudget: 0, budgetMs: BUDGET_MS, p50Ms: null, valuesMs: [], valuesTruncated: false },
  issues: { high: 0, medium: 0, none: 0, unknown: 0 },
  recorded: 0,
}

export type Row = { summary: CallSummary; preview?: Call; started: number; label: string }

export function rowOf(summary: CallSummary, preview?: Call): Row {
  const label = [summary.pipeline !== "unknown" ? pipelineLabel[summary.pipeline] : null, summary.models.slice(0, 2).join(" · ") || null].filter(Boolean).join(" · ")
  return { summary, preview, started: Date.parse(summary.startedAt) / 1000, label }
}
const answersOf = (r: Row) => r.summary.answerMs ?? []
// Unknown when either count is unknown: it never reads as "no issues".
const issueCounts = (r: Row) => (r.summary.issues.high === null || r.summary.issues.medium === null ? null : { high: r.summary.issues.high, medium: r.summary.issues.medium })

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

// The columns the server can sort, by their listCalls sort key. The others do not sort.
export const serverSort: Record<string, string> = { started: "startedAt", length: "durationSeconds", turns: "turnCount", answer: "answerMs", issues: "issues" }

export function columns(tagsOf: (room: string) => string[]): Columns<Row> {
  return [
    {
      id: "room",
      enableSorting: false,
      accessorFn: (r) => r.summary.room,
      header: ({ column }) => <DataGridColumnHeader title="Call" column={column} />,
      size: 300,
      cell: ({ row: { original: r } }) => (
        <div className="min-w-0 py-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-mono text-[12px]">{r.summary.room}</span>
            {r.summary.recorded && <Waveform size={14} className="shrink-0 text-primary" aria-label="Recorded" />}
            {r.preview && <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] text-accent-foreground">Design preview</span>}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">{r.label || "pipeline not reported"}</div>
          {tagsOf(r.summary.room).length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {tagsOf(r.summary.room).map((t) => (
                <TagChip key={t} tag={t} />
              ))}
            </div>
          )}
        </div>
      ),
    },
    {
      id: "channel",
      enableSorting: false,
      accessorFn: (r) => channelInfo[r.summary.channel].label,
      header: ({ column }) => <DataGridColumnHeader title="Channel" column={column} />,
      size: 140,
      cell: ({ row: { original: r } }) => {
        const ch = channelInfo[r.summary.channel]
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
      cell: ({ row: { original: r } }) => <span className="text-[12px] text-muted-foreground tnum">{new Date(r.summary.startedAt).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>,
    },
    {
      id: "length",
      // Unknown sorts below every known length instead of reading as zero.
      accessorFn: (r) => r.summary.durationSeconds ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Length" column={column} />,
      size: 90,
      meta: right,
      cell: ({ row: { original: r } }) => <span className="text-[13px] tnum">{r.summary.durationSeconds !== null ? length(r.summary.durationSeconds) : <span className="text-muted-foreground">?</span>}</span>,
    },
    {
      id: "turns",
      // Unknown sorts below every known count instead of reading as zero.
      accessorFn: (r) => r.summary.turnCount ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Turns" column={column} />,
      size: 80,
      meta: right,
      cell: ({ row: { original: r } }) => <span className="text-[13px] tnum">{r.summary.turnCount ?? <span className="text-muted-foreground">?</span>}</span>,
    },
    {
      id: "answer",
      accessorFn: (r) => r.summary.answerMsMedian ?? -1,
      header: ({ column }) => <DataGridColumnHeader title="Answer time" column={column} />,
      size: 190,
      cell: ({ row: { original: r } }) =>
        r.summary.answerMsMedian !== null ? (
          <div className="flex items-center gap-2">
            <span className={cn("w-12 text-[13px] tnum", r.summary.answerMsMedian > BUDGET_MS && "text-destructive")}>{fmt(r.summary.answerMsMedian)}</span>
            <AnswerDots answers={answersOf(r)} />
          </div>
        ) : (
          <span className="text-[12px] text-muted-foreground">not measured</span>
        ),
    },
    {
      id: "issues",
      // Unknown sorts below "none".
      accessorFn: (r) => {
        const n = issueCounts(r)
        return n ? n.high * 100 + n.medium : -1
      },
      header: ({ column }) => <DataGridColumnHeader title="Issues" column={column} />,
      size: 150,
      cell: ({ row: { original: r } }) => {
        const n = issueCounts(r)
        if (!n) return <span className="text-[12px] text-muted-foreground">unknown</span>
        return n.high + n.medium ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex cursor-help items-center gap-1">
                {n.high > 0 && <span className="rounded-md bg-destructive/10 px-1.5 text-[11px] text-destructive tnum">{n.high} high</span>}
                {n.medium > 0 && <span className="rounded-md bg-amber-500/10 px-1.5 text-[11px] text-amber-700 tnum dark:text-amber-400">{n.medium} medium</span>}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 text-xs">
              <ul className="space-y-0.5">
                {r.summary.issues.titles.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-[12px] text-muted-foreground">none</span>
        )
      },
    },
    { id: "go", header: () => null, size: 32, enableSorting: false, cell: () => <CaretRight className="text-muted-foreground" /> },
  ]
}

// Unknown values (no answer measured, turns not reported) match only "is empty", never a number.
function callFields(allTags: string[], tagsOf: (room: string) => string[]): Field<Row>[] {
  return [
    { id: "room", label: "Room", type: "text", icon: <Hash />, operators: serverOperators.room, get: (r) => r.summary.room },
    { id: "channel", label: "Channel", type: "select", icon: <Phone />, operators: serverOperators.choiceWithNot, options: (Object.keys(channelInfo) as Channel[]).map((c) => ({ value: c, label: channelInfo[c].label })), get: (r) => r.summary.channel },
    {
      id: "issues",
      label: "Issues",
      type: "select",
      icon: <WarningCircle />,
      operators: serverOperators.choice,
      options: [
        { value: "high", label: "High" },
        { value: "medium", label: "Medium" },
        { value: "none", label: "None" },
      ],
      get: (r) => {
        const n = issueCounts(r)
        return n ? (n.high ? "high" : n.medium ? "medium" : "none") : null
      },
    },
    { id: "answer", label: "Answer time (ms)", type: "number", icon: <Timer />, operators: serverOperators.number, get: (r) => r.summary.answerMsMedian },
    { id: "length", label: "Length (s)", type: "number", icon: <Clock />, operators: serverOperators.number, get: (r) => r.summary.durationSeconds },
    { id: "turns", label: "Turns", type: "number", icon: <ChatsCircle />, operators: serverOperators.number, get: (r) => r.summary.turnCount },
    { id: "recorded", label: "Recorded", type: "boolean", icon: <Waveform />, operators: serverOperators.boolean, get: (r) => r.summary.recorded },
    ...(allTags.length ? [{ id: "tag", label: "Tag", type: "multiselect" as const, icon: <Tag />, operators: serverOperators.tags, options: allTags.map((t) => ({ value: t, label: t })), get: (r: Row) => tagsOf(r.summary.room) }] : []),
  ]
}

export function CallsPage() {
  const preview = usePreviewCall()
  // The open call lives in the URL, /calls/<call id>, so a call has a link to share.
  const { callId } = useParams({ strict: false }) as { callId?: string }
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [query, setQuery] = useUrlState<FilterQuery>(
    "q",
    useMemo(() => createFilterQuery([]), []),
  )
  const tags = useCallTags()
  const archived = useArchived()
  const [view, setView] = useUrlState<"active" | "archived">("view", "active")
  // Archive and tags are kept in this browser and served back by the API (see mocks/dispositions.ts),
  // so a change re-reads the list.
  const dispositions = JSON.stringify([archived, tags.snapshot()])
  useEffect(() => void queryClient.invalidateQueries({ queryKey: ["calls"] }), [dispositions, queryClient])

  // The range ends at the project's latest call; a call still open counts from its start.
  const latest = useQuery(latestCallQuery())
  const last = latest.data ? Date.parse(latest.data.endedAt ?? latest.data.startedAt) / 1000 : null
  const now = last ?? Date.now() / 1000
  const [range, setRange] = useUrlRange("7d", now)
  const fields = useMemo(() => callFields(tags.all, tags.of), [tags])
  const labels = useMemo(() => Object.fromEntries(fields.map((f) => [f.id, String(f.label)])), [fields])

  // The range, the filters and the archive tab all run on the server; the cards and the table read the
  // same answer, so they always agree.
  const server = useMemo(() => {
    const f = toServerParams(query, labels)
    f.params.set("from", new Date(range.from * 1000).toISOString())
    // The picker's end is inclusive; the contract's is exclusive.
    f.params.set("to", new Date(range.to * 1000 + 1).toISOString())
    return f
  }, [query, labels, range.from, range.to])
  // The table's sort (in the URL, ?sort=) is the server's: started newest first unless a column says.
  const [sorting] = useUrlState<SortingState>("sort", DEFAULT_SORT)
  const sortKey = sorting[0] && serverSort[sorting[0].id] ? sorting[0] : DEFAULT_SORT[0]
  const withView = (archivedView: boolean, sorted = false) => {
    const p = new URLSearchParams(server.params)
    p.set("archived", String(archivedView))
    if (sorted) {
      p.set("sort", serverSort[sortKey.id])
      p.set("order", sortKey.desc ? "desc" : "asc")
    }
    return p
  }
  // The previous answer stays on screen while a new sort or filter is read, so the table never blanks.
  const list = useInfiniteQuery({ ...callPagesQuery(withView(view === "archived", true)), enabled: !server.nothing, placeholderData: keepPreviousData })
  const otherCount = useQuery({ ...callCountQuery(withView(view !== "archived")), enabled: !server.nothing })
  const first = list.data?.pages[0]
  const total = server.nothing ? 0 : first?.totals.callCount
  const other = server.nothing ? 0 : otherCount.data
  const counts = view === "archived" ? { archived: total, active: other } : { active: total, archived: other }
  const rows = useMemo(
    // The design preview is built in the browser, not filtered or sorted by the server, so it shows only
    // on the unfiltered view, on top.
    () => [
      ...(preview && view === "active" && !query.rules.length ? [rowOf(preview.summary, preview.call)] : []),
      ...(server.nothing ? [] : (list.data?.pages ?? []).flatMap((p) => p.items).map((s) => rowOf(s))),
    ],
    [preview, view, query, list.data, server.nothing],
  )
  // Opening or closing a call keeps the list's filters and range; closing drops the drawer's own state.
  const setOpen = (r: { id: string } | null) =>
    r
      ? navigate({ to: "/calls/$callId", params: { callId: r.id }, search: (p: Record<string, unknown>) => p } as never)
      : navigate({ to: "/calls", search: ({ turn: _t, pane: _p, events: _e, ...rest }: Record<string, unknown>) => rest } as never)
  const archive = (calls: { roomSid: string | null; id: string; room: string }[], to: boolean, done?: () => void) => {
    const before = archived
    const ids = calls.map(keyOf)
    setArchived(to ? [...new Set([...archived, ...ids])] : archived.filter((x) => !ids.includes(x)))
    done?.()
    const what = calls.length === 1 ? calls[0].room : `${calls.length} calls`
    toast(to ? `Archived ${what}` : `Moved ${what} back to calls`, { action: { label: "Undo", onClick: () => setArchived(before) } })
  }
  const t = server.nothing ? NO_CALLS : first?.totals
  const answers = t?.answers
  const channels = (Object.keys(channelInfo) as Channel[]).filter((c) => (t?.byChannel[c] ?? 0) > 0)
  const known = (n: number | null | undefined) => (n === null || n === undefined ? "?" : String(n))
  const flagged = t && t.issues.high !== null && t.issues.medium !== null ? t.issues.high + t.issues.medium : null

  if (latest.isPending) return <PageState loading title="Loading calls" />
  if (latest.isError) return <PageState title="Calls could not be loaded" detail={errorText(latest.error)} retry={() => void latest.refetch()} />
  if (!latest.data && !preview) return <PageState title="No calls yet" detail="Calls appear here once VoiceGateway receives a LiveKit webhook or an agent upload for this project." />

  return (
    <div className="mx-auto max-w-[1200px] space-y-8">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Calls</h1>
          <TimeRangePicker value={range} onChange={setRange} now={now} />
        </div>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <StatCard icon={PhoneCall} value={known(t?.callCount)} label="Calls">
            <div className="flex gap-0.5">
              {channels.map((c) => (
                <span key={c} className="h-2 rounded-sm bg-primary/60" style={{ flex: t?.byChannel[c] ?? 0 }} title={`${channelInfo[c].label}: ${t?.byChannel[c]}`} />
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">{channels.map((c) => `${t?.byChannel[c]} ${channelInfo[c].label.toLowerCase()}`).join(" · ")}</p>
          </StatCard>
          <StatCard icon={Timer} value={!answers || answers.p50Ms === null ? "Unknown" : fmt(answers.p50Ms)} label="Median answer time" alert={answers?.p50Ms != null && answers.p50Ms > answers.budgetMs}>
            <AnswerDots answers={answers?.valuesMs ?? []} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{answers ? `${known(answers.overBudget)} of ${known(answers.turns)} turns over the ${answers.budgetMs / 1000} s budget` : ""}</p>
          </StatCard>
          <StatCard icon={WarningCircle} value={`${known(flagged)} of ${known(t?.callCount)}`} label="Calls with issues" alert={(flagged ?? 0) > 0}>
            <div className="flex gap-0.5">
              {t &&
                ([
                  ["high", "bg-destructive"],
                  ["medium", "bg-amber-500"],
                  ["none", "bg-primary/40"],
                  ["unknown", "bg-muted"],
                ] as const).map(([k, color]) => (t.issues[k] ? <span key={k} className={cn("h-2 rounded-sm", color)} style={{ flex: t.issues[k] ?? 0 }} title={`${k}: ${t.issues[k]}`} /> : null))}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">{t ? `calls by worst finding: ${known(t.issues.high)} high · ${known(t.issues.medium)} medium · ${known(t.issues.unknown)} not checked` : ""}</p>
          </StatCard>
          <StatCard icon={Waveform} value={`${known(t?.recorded)} of ${known(t?.callCount)}`} label="Recorded">
            <p className="text-[11px] text-muted-foreground">from the agent's own upload, opt-in per project</p>
          </StatCard>
        </div>
      </header>

      <section className="space-y-3">
        <Tabs value={view} onValueChange={(v) => setView(v as "active" | "archived")}>
          <TabsList>
            <TabsTrigger value="active">
              Calls <span className="ml-1 text-[11px] text-muted-foreground tnum">{known(counts.active)}</span>
            </TabsTrigger>
            <TabsTrigger value="archived">
              <Archive /> Archived <span className="ml-1 text-[11px] text-muted-foreground tnum">{known(counts.archived)}</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>
        {server.dropped.length > 0 && (
          <p className="text-[12px] text-amber-700 dark:text-amber-400">{`Not applied, the server cannot filter on: ${server.dropped.join(", ")}.`}</p>
        )}
        {server.nothing && <p className="text-[12px] text-muted-foreground">These filters rule each other out, so no call can match.</p>}
        {!server.nothing && list.isError && <PageState title="Calls could not be loaded" detail={errorText(list.error)} retry={() => void list.refetch()} />}
        {!server.nothing && list.isPending && <PageState loading title="Loading calls" />}
        {(server.nothing || list.isSuccess) && (
        <DataTable
          key={view}
          filtered
          server={{ total: total ?? null, hasMore: Boolean(list.hasNextPage), loading: list.isFetchingNextPage, loadMore: () => void list.fetchNextPage() }}
          data={rows}
          columns={columns(tags.of)}
          fields={fields}
          query={query}
          onQueryChange={setQuery}
          getRowId={(r) => keyOf(r.summary)}
          onRowClick={(r) => setOpen(r.summary)}
          sort={DEFAULT_SORT}
          empty={view === "archived" ? "No archived calls." : "No calls match these filters."}
          bulk={(sel, clear) =>
            view === "active" ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  archive(
                    sel.map((r) => r.summary),
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
                    sel.map((r) => r.summary),
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
        )}
        <p className="text-[12px] text-muted-foreground">{`Answer time: caller stops talking to agent audio, per turn. The line marks the ${BUDGET_MS / 1000} s budget, a setting, not a measurement.`}</p>
      </section>
      <Sheet open={callId !== undefined} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent side="right" onOpenAutoFocus={(e) => e.preventDefault()} onPointerDownOutside={keepOpenOnInsidePress} className="gap-0 p-0 data-[side=right]:w-[80vw] data-[side=right]:sm:max-w-none">
          {callId !== undefined && <CallDrawer callId={callId} preview={preview?.call.id === callId ? preview.call : undefined} archived={archived} archive={archive} />}
        </SheetContent>
      </Sheet>
    </div>
  )
}

const errorText = (e: unknown) => (e instanceof ApiError && e.status === 0 ? "The VoiceGateway API did not answer. Check that web is running and reachable from this page." : e instanceof Error ? e.message : "Unknown error")

// The whole page while its data is loading, could not be read, or holds nothing yet.
function PageState({ loading, title, detail, retry }: { loading?: boolean; title: string; detail?: string; retry?: () => void }) {
  return (
    <Empty className="mx-auto max-w-[1200px]">
      <EmptyHeader>
        <EmptyMedia variant="icon">{loading ? <Spinner /> : <PhoneCall />}</EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {detail && <EmptyDescription>{detail}</EmptyDescription>}
      </EmptyHeader>
      {retry && (
        <EmptyContent>
          <Button size="sm" variant="outline" onClick={retry}>
            Try again
          </Button>
        </EmptyContent>
      )}
    </Empty>
  )
}

// One call, read by the id in the URL, whatever the list's filters are. The dev design preview is built
// in the browser.
function CallDrawer({ callId, preview, archived, archive }: { callId: string; preview?: Call; archived: string[]; archive: (calls: { roomSid: string | null; id: string; room: string }[], to: boolean) => void }) {
  const read = useQuery({ ...callQuery(callId), enabled: !preview })
  const call = preview ?? read.data
  if (!call) {
    if (read.error instanceof ApiError && read.error.status === 404) return <DrawerState title="Call not found" detail={`No call ${callId} in this project.`} />
    if (read.isError) return <DrawerState title="The call could not be loaded" detail={errorText(read.error)} />
    return <DrawerState loading title="Loading the call" />
  }
  const isArchived = archived.includes(keyOf(call))
  return (
    <>
      <SheetHeader className="border-b px-5 py-4 pr-14">
        <SheetTitle className="text-lg">{call.room}</SheetTitle>
        <SheetDescription asChild>
          <div>
            <CallMeta call={call} />
          </div>
        </SheetDescription>
        <div className="flex items-center gap-2 pt-1.5">
          <CallTags room={call.room} />
          <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground" onClick={() => archive([call], !isArchived)}>
            {isArchived ? <ArrowCounterClockwise /> : <Archive />}
            {isArchived ? "Unarchive" : "Archive"}
          </Button>
        </div>
      </SheetHeader>
      <div className="min-h-0 flex-1">
        <CallView key={keyOf(call)} call={call} />
      </div>
    </>
  )
}

function DrawerState({ loading, title, detail }: { loading?: boolean; title: string; detail?: string }) {
  return (
    <>
      <SheetHeader className="sr-only">
        <SheetTitle>{title}</SheetTitle>
        <SheetDescription>{detail ?? title}</SheetDescription>
      </SheetHeader>
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">{loading ? <Spinner /> : <PhoneCall />}</EmptyMedia>
          <EmptyTitle>{title}</EmptyTitle>
          {detail && <EmptyDescription>{detail}</EmptyDescription>}
        </EmptyHeader>
      </Empty>
    </>
  )
}
