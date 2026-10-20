import { IssueTags } from "./issue-tags"
import { useMemo } from "react"
import { Link } from "@tanstack/react-router"
import { Archive, Tag, ArrowCounterClockwise, ArrowSquareOut, CaretRight, CheckCircle, Clock, DotsThree, EyeSlash, Phone, PhoneCall, Robot, Shapes, Warning, WarningCircle } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useIssueMetadata } from "./issue-state"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { cn } from "@/lib/utils"
import { type Issue, type Severity, issuesFrom, lastSeen, lastSortKey } from "./issues-model"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/page-state"
import { issuesQuery } from "@/lib/api"
import { useUrlState } from "@/lib/url-state"

// Issues: every finding the console computes, grouped so a pattern across calls reads as one row
// (decision 0021). Open a row for the calls or components it was found on. A standing issue is
// current state (an agent or phone bridge check over the latest read): it reads "Current", not a
// time, and is counted apart from the problems that happened.

const dot: Record<Severity, string> = { high: "bg-destructive", medium: "bg-amber-500", low: "bg-muted-foreground/50", info: "bg-muted-foreground/30" }
const sourceIcon = { Calls: PhoneCall, "Agent workers": Robot, "Phone bridge": Phone }
const day = (s: number) => new Date(s * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

function Hits({ issue }: { issue: Issue }) {
  return (
    <ul className="divide-y">
      {issue.hits.map((h, k) => (
        <li key={k} className="grid grid-cols-[120px_minmax(0,220px)_minmax(0,1fr)] items-baseline gap-4 py-2.5 text-[13px]">
          <span className="text-muted-foreground tnum">{issue.kind === "standing" ? "Current" : h.at === null ? "Unknown" : day(h.at)}</span>
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

function Seen({ i }: { i: Issue }) {
  const n = i.hits.length
  return <span className="tnum">{`${n} ${i.source === "Calls" ? (n === 1 ? "call" : "calls") : n === 1 ? "place" : "places"}`}</span>
}
function Last({ i }: { i: Issue }) {
  const t = lastSeen(i)
  if (i.kind === "standing")
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help">Current</span>
        </TooltipTrigger>
        <TooltipContent className="text-xs">True of the latest read whatever the range, not an event in it.</TooltipContent>
      </Tooltip>
    )
  return <span className="tnum">{t === null ? "Unknown" : day(t)}</span>
}

function TitleCell({ i, regressed = false }: { i: Issue; regressed?: boolean }) {
  return (
    <>
      <span className="flex items-center gap-2 text-[14px] font-medium">
        <span className={cn("size-2 shrink-0 rounded-full", dot[i.severity])} role="img" aria-label={i.severity} />
        {i.title}
        {regressed && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="rounded-full bg-destructive/10 px-1.5 text-[11px] font-medium text-destructive">Regressed</span>
            </TooltipTrigger>
            <TooltipContent className="text-xs">Resolved, then seen again after that.</TooltipContent>
          </Tooltip>
        )}
      </span>
      {/* At phone width the other columns hide; where and when it was seen stack here instead. */}
      <span className="mt-0.5 flex flex-wrap gap-x-3 pl-4 text-[12px] text-muted-foreground sm:hidden">
        <span>{i.source}</span>
        <Seen i={i} />
        <Last i={i} />
      </span>
    </>
  )
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
          <IssueTags issue={i} />
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
    cell: ({ row }) => <TitleCell i={row.original} />,
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
    cell: ({ row }) => (
      <span className="text-[13px] whitespace-nowrap">
        <Seen i={row.original} />
      </span>
    ),
  },
  {
    id: "last",
    accessorFn: lastSortKey,
    header: ({ column }) => <DataGridColumnHeader title="Last" column={column} />,
    meta: { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right" },
    cell: ({ row }) => (
      <span className="text-[13px] whitespace-nowrap text-muted-foreground">
        <Last i={row.original} />
      </span>
    ),
  },
]

const severityOptions = (["high", "medium", "low", "info"] as const).map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))
const fields: Field<Issue>[] = [
 { id:"tags",label:"Tags",type:"text",icon:<Tag/>,get:(i)=>i.metadata.tags.length?i.metadata.tags.join(" "):null },
  { id: "severity", label: "Severity", type: "select", icon: <Warning />, options: severityOptions, get: (i) => i.severity },
  {
    id: "source",
    label: "Source",
    type: "select",
    icon: <Shapes />,
    options: (["Calls", "Agent workers", "Phone bridge"] as const).map((v) => ({ value: v, label: v })),
    get: (i) => i.source,
  },
  {
    id: "kind",
    label: "Kind",
    type: "select",
    icon: <Clock />,
    options: [
      { value: "event", label: "Happened in range" },
      { value: "standing", label: "Current state" },
    ],
    get: (i) => i.kind,
  },
  { id: "seen", label: "Seen", type: "number", icon: <PhoneCall />, get: (i) => i.hits.length },
]

type View = "open" | "resolved" | "ignored" | "archived"
const viewLabel: Record<View, string> = { open: "Open", resolved: "Resolved", ignored: "Ignored", archived: "Archived" }

export function IssuesPage() {
  const read = useQuery(issuesQuery())
  const all = useMemo(() => (read.data ? issuesFrom(read.data) : null), [read.data])
  if (!all || read.isError) return <QueryState icon={WarningCircle} what="issues" query={read} />
  return <IssuesView all={all} complete={read.data?.complete ?? false} />
}

function IssuesView({ all, complete }: { all: Issue[]; complete: boolean }) {
  const metadata = useIssueMetadata()
  const [view, setView] = useUrlState<View>("view", "open")
  const withStatus = all.map((i) => ({ i, st: { status: i.metadata.state, regressed: i.metadata.regressed } }))
  const count = (v: View) => withStatus.filter((x) => v === "archived" ? x.i.metadata.archived : !x.i.metadata.archived && x.st.status === v).length
  const shown = withStatus.filter((x) => view === "archived" ? x.i.metadata.archived : !x.i.metadata.archived && x.st.status === view).map((x) => x.i)
  const regressed = new Set(withStatus.filter((x) => x.st.regressed).map((x) => x.i.id))
  const openAll = withStatus.filter((x) => !x.i.metadata.archived && x.st.status === "open").map((x) => x.i)
  // The headline counts what happened; standing issues are current state and counted apart.
  const open = openAll.filter((i) => i.kind === "event")
  const current = openAll.filter((i) => i.kind === "standing")
  const calls = new Set(open.flatMap((i) => i.hits.map((h) => h.callId).filter(Boolean))).size
  const high = open.filter((i) => i.severity === "high").length
  const currentHigh = current.filter((i) => i.severity === "high").length

  const apply = async (list: Issue[], state: "resolved" | "ignored" | "open" | "archive" | "restore", done?: () => void) => {
    const saved: Awaited<ReturnType<typeof metadata.mutateAsync>>[] = []
    let failed = 0
    for (const issue of list) {
      try { saved.push(await metadata.mutateAsync({id:issue.id,before:issue.metadata,...(state === "archive" || state === "restore" ? {archived:state === "archive"} : {state})})) }
      catch (error) { failed++; toast.error(error instanceof Error ? error.message : "Issue save failed.") }
    }
    if (failed === 0) done?.()
    if (!saved.length) return
    toast(`${saved.length} ${saved.length === 1 ? "issue" : "issues"} ${state === "open" ? "reopened" : state === "archive" ? "archived" : state === "restore" ? "restored" : state}${failed ? `; ${failed} failed` : ""}`, {
      action: {label:"Undo",onClick:async()=>{
        for (const edit of saved) {
          try {await metadata.mutateAsync({id:edit.id,before:edit.saved,undo:edit})}
          catch (error) {toast.error(error instanceof Error ? error.message : "Undo failed.")}
        }
      }},
    })
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
            {metadata.canEdit && view !== "resolved" && (
              <DropdownMenuItem disabled={metadata.isPending} onSelect={() => void apply([row.original], "resolved")}>
                <CheckCircle /> Resolve
              </DropdownMenuItem>
            )}
            {metadata.canEdit && view !== "ignored" && (
              <DropdownMenuItem disabled={metadata.isPending} onSelect={() => void apply([row.original], "ignored")}>
                <EyeSlash /> Ignore
              </DropdownMenuItem>
            )}
            {metadata.canEdit && view !== "open" && (
              <DropdownMenuItem disabled={metadata.isPending} onSelect={() => void apply([row.original], "open")}>
                <ArrowCounterClockwise /> Reopen
              </DropdownMenuItem>
            )}
            {metadata.canEdit && <DropdownMenuItem disabled={metadata.isPending} onSelect={()=>void apply([row.original],row.original.metadata.archived?"restore":"archive")}><Archive/>{row.original.metadata.archived?"Restore":"Archive"}</DropdownMenuItem>}
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
            cell: ({ row }: { row: { original: Issue } }) => <TitleCell i={row.original} regressed={regressed.has(row.original.id)} />,
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
        <p className="text-[13px] text-muted-foreground">
          {current.length
            ? `Plus ${current.length} current ${current.length === 1 ? "state" : "states"}${currentHigh ? `, ${currentHigh} high,` : ""} on agent workers and the phone bridge, true now whatever the range.`
            : complete ? "No open current state on agent workers or the phone bridge." : "Standing-source coverage is incomplete."}
        </p>
      </header>
      {!complete && <p role="status" className="text-[13px] text-muted-foreground">Source coverage is incomplete. Listed findings are recorded evidence; missing findings do not establish recovery.</p>}
      <Tabs value={view} onValueChange={(v) => setView(v as View)}>
        <TabsList>
          {(["open", "resolved", "ignored", "archived"] as View[]).map((v) => (
            <TabsTrigger key={v} value={v}>
              {viewLabel[v]} <span className="ml-1 text-[11px] text-muted-foreground tnum">{count(v)}</span>
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent key={view} value={view}>
          <DataTable
            key={view}
            data={shown}
            columns={cols}
            fields={fields}
            getRowId={(i) => i.id}
            canExpand={() => true}
            phoneColumns={["expand", "title", "actions"]}
            empty={view === "open" ? "No open issue matches these filters." : `No ${view} issues.`}
            bulk={(sel, clear) => (
              <>
                {metadata.canEdit && <Button size="sm" variant="outline" disabled={metadata.isPending} onClick={()=>void apply(sel,view === "archived"?"restore":"archive",clear)}><Archive/>{view === "archived"?"Restore":"Archive"}</Button>}
                {metadata.canEdit && view !== "resolved" && (
                  <Button size="sm" variant="outline" disabled={metadata.isPending} onClick={() => void apply(sel, "resolved", clear)}>
                    <CheckCircle /> Resolve
                  </Button>
                )}
                {metadata.canEdit && view !== "ignored" && (
                  <Button size="sm" variant="outline" disabled={metadata.isPending} onClick={() => void apply(sel, "ignored", clear)}>
                    <EyeSlash /> Ignore
                  </Button>
                )}
                {metadata.canEdit && view !== "open" && (
                  <Button size="sm" variant="outline" disabled={metadata.isPending} onClick={() => void apply(sel, "open", clear)}>
                    <ArrowCounterClockwise /> Reopen
                  </Button>
                )}
              </>
            )}
          />
        </TabsContent>
      </Tabs>
      <p className="text-[12px] text-muted-foreground">Decisions are shared across the project. A resolved issue regresses when new evidence is observed. "Current" is a check on the latest read of agents or the phone bridge, not an event with a time.</p>
    </div>
  )
}
