import { useMemo } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowCounterClockwise, ArrowSquareOut, CaretRight, CheckCircle, DotsThree, EyeSlash, Phone, PhoneCall, Robot, Shapes, Warning, WarningCircle } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { type Store, statusOf, storeFrom, useSaveIssueStates } from "./issue-state"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { cn } from "@/lib/utils"
import { type Issue, type Severity, issuesFrom } from "./issues-model"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { issueDispositionsQuery, issuesQuery } from "@/lib/api"
import { useUrlState } from "@/lib/url-state"

// Issues: every finding the console computes, grouped so a pattern across calls reads as one row
// (decision 0021). Open a row for the calls or components it was found on.

const dot: Record<Severity, string> = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/50", info: "bg-muted-foreground/30" }
const sourceIcon = { Calls: PhoneCall, "Agent workers": Robot, "Phone bridge": Phone }
const day = (s: number) => new Date(s * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

function Hits({ issue }: { issue: Issue }) {
  return (
    <ul className="divide-y">
      {issue.hits.map((h, k) => (
        <li key={k} className="grid grid-cols-[120px_minmax(0,220px)_minmax(0,1fr)] items-baseline gap-4 py-2.5 text-[13px]">
          <span className="text-muted-foreground tnum">{h.at === null ? "Now" : day(h.at)}</span>
          {h.callId ? (
            <Link to="/calls/$callId" params={{ callId: h.callId }} className="truncate font-mono text-[12px] hover:underline">
              {h.where}
            </Link>
          ) : (
            <Link to={issue.to} className="truncate font-mono text-[12px] hover:underline">
              {h.where}
            </Link>
          )}
          <span className="text-muted-foreground">{h.detail}</span>
        </li>
      ))}
    </ul>
  )
}

const lastSeen = (i: Issue) => {
  const t = Math.max(...i.hits.map((h) => h.at ?? -Infinity))
  return Number.isFinite(t) ? t : null
}

const columns: Columns<Issue> = [
  {
    id: "expand",
    header: () => null,
    size: 32,
    cell: ({ row }) => <CaretRight size={12} className={cn("text-muted-foreground transition-transform", row.getIsExpanded() && "rotate-90")} />,
    meta: {
      expandedContent: (i: Issue) => (
        <div className="bg-muted/30 px-10 py-1">
          <Hits issue={i} />
        </div>
      ),
    },
  },
  {
    id: "title",
    accessorFn: (i) => i.title,
    header: ({ column }) => <DataGridColumnHeader title="Issue" column={column} />,
    size: 420,
    cell: ({ row }) => (
      <span className="flex items-center gap-2 text-[14px] font-medium">
        <span className={cn("size-2 shrink-0 rounded-full", dot[row.original.severity])} aria-label={row.original.severity} />
        {row.original.title}
      </span>
    ),
  },
  {
    id: "source",
    accessorFn: (i) => i.source,
    header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
    cell: ({ row }) => {
      const S = sourceIcon[row.original.source]
      return (
        <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
          <S size={14} />
          {row.original.source}
        </span>
      )
    },
  },
  {
    id: "seen",
    accessorFn: (i) => i.hits.length,
    header: ({ column }) => <DataGridColumnHeader title="Seen" column={column} />,
    meta: { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" },
    cell: ({ row }) => {
      const n = row.original.hits.length
      return (
        <span className="text-[13px] tnum">
          {n} {row.original.source === "Calls" ? (n === 1 ? "call" : "calls") : n === 1 ? "place" : "places"}
        </span>
      )
    },
  },
  {
    id: "last",
    accessorFn: (i) => lastSeen(i) ?? -1,
    header: ({ column }) => <DataGridColumnHeader title="Last" column={column} />,
    meta: { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" },
    cell: ({ row }) => {
      const t = lastSeen(row.original)
      return <span className="text-[13px] text-muted-foreground tnum">{t === null ? "Now" : day(t)}</span>
    },
  },
]

const severityOptions = (["high", "medium", "low", "info"] as const).map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))
const fields: Field<Issue>[] = [
  { id: "severity", label: "Severity", type: "select", icon: <Warning />, options: severityOptions, get: (i) => i.severity },
  {
    id: "source",
    label: "Source",
    type: "select",
    icon: <Shapes />,
    options: (["Calls", "Agent workers", "Phone bridge"] as const).map((v) => ({ value: v, label: v })),
    get: (i) => i.source,
  },
  { id: "seen", label: "Seen", type: "number", icon: <PhoneCall />, get: (i) => i.hits.length },
]

type View = "open" | "resolved" | "ignored"
const viewLabel: Record<View, string> = { open: "Open", resolved: "Resolved", ignored: "Ignored" }

export function IssuesPage() {
  const read = useQuery(issuesQuery())
  const decided = useQuery(issueDispositionsQuery())
  const all = useMemo(() => (read.data ? issuesFrom(read.data) : null), [read.data])
  const states = useMemo(() => (decided.data ? storeFrom(decided.data) : null), [decided.data])
  if (!all) return <QueryState icon={WarningCircle} what="issues" query={read} />
  if (!states) return <QueryState icon={WarningCircle} what="issue decisions" query={decided} />
  return <IssuesView all={all} states={states} />
}

function IssuesView({ all, states }: { all: Issue[]; states: Store }) {
  const setIssueStates = useSaveIssueStates()
  const [view, setView] = useUrlState<View>("view", "open")
  const withStatus = all.map((i) => ({ i, st: statusOf(states, i.id, lastSeen(i)) }))
  const count = (v: View) => withStatus.filter((x) => x.st.status === v).length
  const shown = withStatus.filter((x) => x.st.status === view).map((x) => x.i)
  const regressed = new Set(withStatus.filter((x) => x.st.regressed).map((x) => x.i.id))
  const open = withStatus.filter((x) => x.st.status === "open").map((x) => x.i)
  const calls = new Set(open.flatMap((i) => i.hits.map((h) => h.callId).filter(Boolean))).size
  const high = open.filter((i) => i.severity === "high").length

  // Every change can be undone from its toast; the store keeps the state from before.
  const apply = (list: Issue[], state: "resolved" | "ignored" | "open", done?: () => void) => {
    let before: Store = {}
    setIssueStates((current) => {
      before = current
      const next = { ...current }
      for (const i of list) {
        if (state === "open") delete next[i.id]
        else next[i.id] = { state, at: Date.now() }
      }
      return next
    })
    done?.()
    const what = list.length === 1 ? `"${list[0].title}"` : `${list.length} issues`
    toast(state === "open" ? `Reopened ${what}` : state === "resolved" ? `Resolved ${what}` : `Ignored ${what}`, { action: { label: "Undo", onClick: () => setIssueStates(before) } })
  }

  const rowMenu: Columns<Issue>[number] = {
    id: "actions",
    size: 44,
    enableSorting: false,
    header: () => null,
    cell: ({ row }) => (
      <span onClick={(e) => e.stopPropagation()} className="flex justify-end">
        <DropdownMenu>
          <DropdownMenuTrigger className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Issue actions">
            <DotsThree size={18} weight="bold" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-44">
            {view !== "resolved" && (
              <DropdownMenuItem onSelect={() => apply([row.original], "resolved")}>
                <CheckCircle /> Resolve
              </DropdownMenuItem>
            )}
            {view !== "ignored" && (
              <DropdownMenuItem onSelect={() => apply([row.original], "ignored")}>
                <EyeSlash /> Ignore
              </DropdownMenuItem>
            )}
            {view !== "open" && (
              <DropdownMenuItem onSelect={() => apply([row.original], "open")}>
                <ArrowCounterClockwise /> Reopen
              </DropdownMenuItem>
            )}
            <DropdownMenuItem asChild>
              <Link to={row.original.to}>
                <ArrowSquareOut /> Open {row.original.source}
              </Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </span>
    ),
  }
  const cols: Columns<Issue> = [
    ...columns.map((c) =>
      c.id === "title"
        ? {
            ...c,
            cell: ({ row }: { row: { original: Issue } }) => (
              <span className="flex items-center gap-2 text-[14px] font-medium">
                <span className={cn("size-2 shrink-0 rounded-full", dot[row.original.severity])} aria-label={row.original.severity} />
                {row.original.title}
                {regressed.has(row.original.id) && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="rounded-full bg-destructive/10 px-1.5 text-[11px] font-medium text-destructive">Regressed</span>
                    </TooltipTrigger>
                    <TooltipContent className="text-xs">Resolved, then seen again after that.</TooltipContent>
                  </Tooltip>
                )}
              </span>
            ),
          }
        : c,
    ),
    rowMenu,
  ] as Columns<Issue>

  return (
    <div className="mx-auto max-w-[1200px] space-y-6">
      <header className="space-y-1">
        <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance">
          {open.length} open{high ? `, ${high} high` : ""}. <span className="text-muted-foreground">{calls ? `On ${calls} calls and the components around them.` : "Nothing open on calls."}</span>
        </h1>
      </header>
      <Tabs value={view} onValueChange={(v) => setView(v as View)}>
        <TabsList>
          {(["open", "resolved", "ignored"] as View[]).map((v) => (
            <TabsTrigger key={v} value={v}>
              {viewLabel[v]} <span className="ml-1 text-[11px] text-muted-foreground tnum">{count(v)}</span>
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <DataTable
        key={view}
        data={shown}
        columns={cols}
        fields={fields}
        getRowId={(i) => i.id}
        canExpand={() => true}
        empty={view === "open" ? "No open issue matches these filters." : `No ${view} issues.`}
        bulk={(sel, clear) => (
          <>
            {view !== "resolved" && (
              <Button size="sm" variant="outline" onClick={() => apply(sel, "resolved", clear)}>
                <CheckCircle /> Resolve
              </Button>
            )}
            {view !== "ignored" && (
              <Button size="sm" variant="outline" onClick={() => apply(sel, "ignored", clear)}>
                <EyeSlash /> Ignore
              </Button>
            )}
            {view !== "open" && (
              <Button size="sm" variant="outline" onClick={() => apply(sel, "open", clear)}>
                <ArrowCounterClockwise /> Reopen
              </Button>
            )}
          </>
        )}
      />
      <p className="text-[12px] text-muted-foreground">Resolved and ignored are kept in this browser until the server stores them. A resolved issue reopens as regressed when it is seen again. "Now" is a check on current configuration.</p>
    </div>
  )
}
