import { Buildings, CaretRight, ShieldCheck, UsersThree } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { type Columns, DataTable, type Field } from "@/components/data-table"
import { PageState, QueryState, errorText } from "@/components/page-state"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { Badge } from "@/components/ui/badge"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { adminOrganisationsQuery, adminUsageQuery, adminUsersQuery } from "@/lib/api"
import type { AdminOrganisation, AdminUser } from "@/lib/api-types"
import { fmtDateTime } from "@/lib/format"
import { isInstanceAdmin, useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

// The Admin group (decision 0041): pages about the whole instance, for an instance admin. The flag is
// on the account and set from the command line; the server refuses everyone else, so these pages only
// decide what to show.

const right = { headerClassName: "[&>div]:w-full [&>div]:justify-end", cellClassName: "text-right tnum" }
const count = new Intl.NumberFormat()
const USAGE_DAYS = 30

/** What an account without the flag sees at an admin address: the same as a page that does not exist. */
export function NotAnAdmin() {
  return (
    <Empty className="mx-auto max-w-[1200px]">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <ShieldCheck />
        </EmptyMedia>
        <EmptyTitle>No such page</EmptyTitle>
        <EmptyDescription>This address is not a page you can open.</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

function Heading({ title, note }: { title: string; note: string }) {
  return (
    <header className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">{title}</h1>
        <span className="rounded-full bg-muted px-2.5 py-0.5 text-[13px] text-muted-foreground">Instance admin</span>
      </div>
      <p className="text-[13px] text-muted-foreground">{note}</p>
    </header>
  )
}

// The server lists the newest 500 and counts them all, so a longer list says what it leaves out.
const cut = (shown: number, total: number, what: string) => (total > shown ? `Showing the newest ${count.format(shown)} of ${count.format(total)} ${what}.` : null)

// An organisation's call time is read when its row opens, one organisation at a time.
function Usage({ org }: { org: AdminOrganisation }) {
  const usage = useQuery(adminUsageQuery(org.id, USAGE_DAYS))
  if (usage.isPending) return <p className="text-[13px] text-muted-foreground">Reading the last {USAGE_DAYS} days of calls.</p>
  if (usage.isError) return <p className="text-[13px] text-muted-foreground">{`Call time could not be read. ${errorText(usage.error)}`}</p>
  const u = usage.data
  const items: [string, string][] = [
    ["Profiled call minutes", count.format(Math.floor(u.callSeconds / 60))],
    ["Calls measured", count.format(u.calls)],
    ["Ended by inference", count.format(u.inferredCalls)],
    ["Still open", count.format(u.unendedCalls)],
    ["Not measurable", count.format(u.unmeasuredCalls)],
  ]
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-5">
        {items.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[11px] text-muted-foreground">{label}</dt>
            <dd className="text-[18px] font-semibold tnum">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-[12px] text-muted-foreground">{`Calls that ended in the last ${USAGE_DAYS} days. Open and unmeasurable calls are counted apart and add no minutes.`}</p>
    </div>
  )
}

const organisationColumns: Columns<AdminOrganisation> = [
  {
    id: "name",
    accessorFn: (o) => o.name,
    size: 260,
    header: ({ column }) => <DataGridColumnHeader title="Organisation" column={column} />,
    cell: ({ row }) => (
      <span className="flex min-w-0 items-center gap-2">
        <CaretRight size={12} className={cn("shrink-0 text-muted-foreground transition-transform", row.getIsExpanded() && "rotate-90")} />
        <span className="min-w-0">
          <span className="block truncate text-[13.5px] font-medium">{row.original.name}</span>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{row.original.id}</span>
        </span>
      </span>
    ),
    meta: {
      expandedContent: (o: AdminOrganisation) => (
        <div className="bg-muted/30 p-4">
          <Usage org={o} />
        </div>
      ),
    },
  },
  { id: "members", accessorFn: (o) => o.members, size: 110, header: ({ column }) => <DataGridColumnHeader title="Members" column={column} />, cell: ({ row }) => count.format(row.original.members), meta: right },
  { id: "admins", accessorFn: (o) => o.admins, size: 110, header: ({ column }) => <DataGridColumnHeader title="Admins" column={column} />, cell: ({ row }) => count.format(row.original.admins), meta: right },
  { id: "projects", accessorFn: (o) => o.projects, size: 110, header: ({ column }) => <DataGridColumnHeader title="Projects" column={column} />, cell: ({ row }) => count.format(row.original.projects), meta: right },
  { id: "created", accessorFn: (o) => o.createdAt, size: 170, header: ({ column }) => <DataGridColumnHeader title="Created" column={column} />, cell: ({ row }) => <span className="text-muted-foreground">{fmtDateTime(row.original.createdAt)}</span> },
]

const organisationFields: Field<AdminOrganisation>[] = [
  { id: "name", label: "Name", type: "text", get: (o) => o.name },
  { id: "id", label: "Id", type: "text", get: (o) => o.id },
  { id: "members", label: "Members", type: "number", get: (o) => o.members },
  { id: "projects", label: "Projects", type: "number", get: (o) => o.projects },
]

export function AdminOrganisationsPage() {
  const admin = isInstanceAdmin(useSession())
  const read = useQuery({ ...adminOrganisationsQuery(), enabled: admin })
  if (!admin) return <NotAnAdmin />
  if (!read.data) return <QueryState icon={Buildings} what="the organisations" query={read} />
  const { items, total } = read.data
  const note = cut(items.length, total, "organisations")
  return (
    <div className="space-y-6">
      <Heading title="Organisations" note={`${count.format(total)} on this instance. Open a row for its call time.`} />
      {total === 0 ? (
        <PageState icon={Buildings} title="No organisation yet" detail="The first one appears here once it is created." />
      ) : (
        <DataTable data={items} columns={organisationColumns} fields={organisationFields} getRowId={(o) => o.id} canExpand={() => true} sort={[{ id: "created", desc: true }]} phoneColumns={["name", "members"]} empty="No organisation matches these filters." />
      )}
      {note && <p className="text-[12px] text-muted-foreground">{note}</p>}
    </div>
  )
}

const userColumns: Columns<AdminUser> = [
  {
    id: "email",
    accessorFn: (u) => u.email,
    size: 280,
    header: ({ column }) => <DataGridColumnHeader title="Account" column={column} />,
    cell: ({ row }) => (
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate text-[13.5px] font-medium">{row.original.email}</span>
        {row.original.instanceAdmin && <Badge variant="secondary">Instance admin</Badge>}
      </span>
    ),
  },
  {
    id: "organisations",
    accessorFn: (u) => u.organisations.length,
    size: 320,
    header: ({ column }) => <DataGridColumnHeader title="Organisations" column={column} />,
    cell: ({ row }) =>
      row.original.organisations.length === 0 ? (
        <span className="text-muted-foreground">None</span>
      ) : (
        <span className="flex flex-wrap gap-1.5">
          {row.original.organisations.map((o) => (
            <span key={o.id} className="rounded-md bg-muted px-1.5 py-0.5 text-[12px]">
              <span className="font-mono">{o.id}</span>
              <span className="text-muted-foreground">{` ${o.role}`}</span>
            </span>
          ))}
        </span>
      ),
  },
  { id: "created", accessorFn: (u) => u.createdAt, size: 170, header: ({ column }) => <DataGridColumnHeader title="Created" column={column} />, cell: ({ row }) => <span className="text-muted-foreground">{fmtDateTime(row.original.createdAt)}</span> },
]

const userFields: Field<AdminUser>[] = [
  { id: "email", label: "Email", type: "text", get: (u) => u.email },
  { id: "organisation", label: "Organisation", type: "text", get: (u) => u.organisations.map((o) => o.id).join(" ") || null },
  { id: "organisations", label: "Organisations", type: "number", get: (u) => u.organisations.length },
]

export function AdminUsersPage() {
  const admin = isInstanceAdmin(useSession())
  const read = useQuery({ ...adminUsersQuery(), enabled: admin })
  if (!admin) return <NotAnAdmin />
  if (!read.data) return <QueryState icon={UsersThree} what="the accounts" query={read} />
  const { items, total } = read.data
  const note = cut(items.length, total, "accounts")
  return (
    <div className="space-y-6">
      <Heading title="Users" note={`${count.format(total)} accounts on this instance, with each one's organisations and roles.`} />
      <DataTable data={items} columns={userColumns} fields={userFields} getRowId={(u) => u.id} sort={[{ id: "created", desc: true }]} phoneColumns={["email"]} empty="No account matches these filters." />
      {note && <p className="text-[12px] text-muted-foreground">{note}</p>}
    </div>
  )
}

// One of the operator's own pages (VOICEGATEWAY_ADMIN_PAGES), shown inside the shell. The path comes
// only from the session, which the server fills from configuration and checks to be on this site.
export function AdminFramedPage() {
  const { slug } = useParams({ strict: false }) as { slug?: string }
  const session = useSession()
  const page = isInstanceAdmin(session) ? session?.adminPages?.[Number(slug) - 1] : undefined
  if (!page || !page.path.startsWith("/") || page.path.startsWith("//")) return <NotAnAdmin />
  return (
    <div className="space-y-6">
      <Heading title={page.label} note="A page this instance's operator added." />
      <iframe title={page.label} src={page.path} className="h-[calc(100dvh-13rem)] min-h-[520px] w-full rounded-xl border bg-background" />
    </div>
  )
}
