import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleHalf, DotsThree, Stack, EnvelopeSimple as Envelope, WebhooksLogo, Buildings, Database, Key as KeyIcon, Clock, EnvelopeSimple, GearSix, FrameCorners, Lock, UsersThree, X, Plug, PlugsConnected, Plus, TreeStructure, User } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ProviderLogo } from "@/components/provider-logo"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemSeparator, ItemTitle } from "@/components/ui/item"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, SelectGroup, SelectLabel } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider } from "@/components/ui/sidebar"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Switch } from "@/components/ui/switch"
import { useTheme } from "@/components/theme"
import { NewProjectDialog } from "@/components/new-project-dialog"
import { currentOrg, currentScope, scopePrefix, useUrlState } from "@/lib/url-state"
import { type Size, type WidgetId, sizes, widgets } from "@/features/embed/widgets"
import { DEFAULTS, type Thresholds as T } from "@/lib/thresholds"
import { ApiError, callTotalsQuery, connectionQuery, createInvitation, createPasswordReset, createProjectKey, projectKeysQuery, revokeInvitation, revokeProjectKey, invitationsQuery, latestMatchingCallQuery, mediaServerQuery, membersQuery, projectSettingsQuery, saveProjectSettings, removeMember, updateMember, saveAllowedOrigins, createEmbedToken } from "@/lib/api"
import type { CreateEmbedToken, Invitation, Member, ProjectKey, ProjectSettings } from "@/lib/api-types"
import { DataTable, type Columns, type Field as FilterField } from "@/components/data-table"
import { DataGridColumnHeader } from "@/components/reui/data-grid/data-grid-column-header"
import { mod } from "@/components/command-palette"
import { CopyCommand } from "@/components/copy-command"
import { type AccountProfile, ProfileError, readAccountProfile, saveAccountProfile, refreshSession, roleIn, useSession } from "@/lib/session"
import { UsageAllowance } from "@/components/usage-allowance"
import { errorText } from "@/components/page-state"
import { ConnectAgentPanel } from "@/features/connection/connect-agent"
import { ChangeLiveKitKey } from "@/features/connection/livekit-credentials"
import { useHosted } from "@/lib/hosted"

// Settings as a modal over the page you are on (layout from shadcn's sidebar-13 block). Three groups,
// as in ShipVoice Pro's settings: the project picked at the top of the sidebar (integrations, data,
// thresholds), the organisation (its projects, Enterprise shown locked per decision 0018, and Access:
// people, API keys, MCP and the audit log as tabs) and you. Thresholds persist per project
// through the API; unavailable deployment policies remain explicitly unknown.

// The organisation and its projects as getWorkspaces named them (lib/workspaces.ts). When it named
// none, the scope's id stands in and no project is listed.
const org = () => currentOrg() ?? { id: currentScope().org, name: currentScope().org, projects: [] }

type Key = "account" | "organisation" | "access" | "connection" | "integrations" | "embeds" | "data" | "thresholds"
type Page = { key: Key; label: string; icon: Icon; scope: "project" | "organisation" | "you" }
const groups: { label: string; scope: Page["scope"]; pages: Page[] }[] = [
  {
    label: "Project",
    scope: "project",
    pages: [
      { key: "integrations", label: "Integrations", icon: Plug, scope: "project" },
      { key: "connection", label: "Connection", icon: PlugsConnected, scope: "project" },
      { key: "embeds", label: "Embeds", icon: FrameCorners, scope: "project" },
      { key: "data", label: "Data", icon: Database, scope: "project" },
      { key: "thresholds", label: "Thresholds", icon: Clock, scope: "project" },
    ],
  },
  {
    label: "Organisation",
    scope: "organisation",
    pages: [
      { key: "organisation", label: "Organisation", icon: Buildings, scope: "organisation" },
      { key: "access", label: "Access", icon: UsersThree, scope: "organisation" },
    ],
  },
  { label: "You", scope: "you", pages: [{ key: "account", label: "Your account", icon: User, scope: "you" }] },
]

// --- Building blocks: a page header, grouped rows (shadcn Item), and a copyable field. ---

// Untitled UI's settings pattern: a section title with supporting text over a divider, then rows of
// label and hint on the left, value or control on the right, separated by hairlines.
function Header({ title, description, badge }: { title: string; description: string; badge?: string }) {
  return (
    <div className="flex items-start justify-between gap-6 border-b pb-5">
      <div className="space-y-1">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em]">{title}</h2>
        <p className="max-w-[60ch] text-[14px] text-muted-foreground">{description}</p>
      </div>
      {badge && (
        <Badge variant="outline" className="mt-1 shrink-0 font-normal text-muted-foreground">
          {badge}
        </Badge>
      )}
    </div>
  )
}

function Group({ label, children, footer }: { label?: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <section className="pt-6">
      {label && <h3 className="pb-1 text-[14px] font-semibold">{label}</h3>}
      <ItemGroup className="gap-0 has-data-[size=sm]:gap-0">{children}</ItemGroup>
      {footer && <p className="pt-3 text-[13px] text-muted-foreground">{footer}</p>}
    </section>
  )
}

function Row({ title, description, value, mono, icon: I, muted, last }: { title: string; description?: React.ReactNode; value?: React.ReactNode; mono?: boolean; icon?: Icon; muted?: boolean; last?: boolean }) {
  return (
    <>
      <Item role="listitem" size="sm" className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 rounded-none border-0 px-0 py-4">
        <ItemContent className="flex-row items-start gap-3">
          {I && (
            <ItemMedia variant="icon" className={muted ? "mt-0.5 text-muted-foreground" : "mt-0.5"}>
              <I />
            </ItemMedia>
          )}
          <div className="space-y-0.5">
            <ItemTitle className={muted ? "text-[14px] font-medium text-muted-foreground" : "text-[14px] font-medium"}>{title}</ItemTitle>
            {description && <ItemDescription className="line-clamp-none text-[13px] leading-snug">{description}</ItemDescription>}
          </div>
        </ItemContent>
        {value !== undefined && <ItemActions className={mono ? "justify-start font-mono text-[13px]" : "justify-start text-[14px]"}>{value}</ItemActions>}
      </Item>
      {!last && <ItemSeparator className="my-0" />}
    </>
  )
}
const NotSet = () => <span className="text-muted-foreground">Not set</span>
const Muted = ({ children }: { children: React.ReactNode }) => <span className="text-muted-foreground">{children}</span>

// The project's LiveKit server as getMediaServer reports it.
function ServerUrl({ projectId }: { projectId: string }) {
  const read = useQuery(mediaServerQuery({ ...currentScope(), project: projectId }))
  if (read.isPending) return <Muted>Loading</Muted>
  if (read.isError) return <Muted>Could not be read</Muted>
  return read.data.server ? <span className="font-mono text-[13px]">{read.data.server.url}</span> : <Muted>not reported</Muted>
}

// --- Pages ---

function AccountName() {
  const session = useSession()
  const client = useQueryClient()
  const key = ["account-profile", session?.email]
  const read = useQuery({ queryKey: key, queryFn: ({ signal }) => readAccountProfile(signal), enabled: !!session, staleTime: 15_000, retry: false })
  const [edit, setEdit] = useState<{ name: string; revision: number } | null>(null)
  const name = edit?.name ?? read.data?.name ?? ""
  const valid = Array.from(name.trim()).length <= 100 && !/\p{Cc}/u.test(name)
  const save = useMutation({
    mutationFn: () => saveAccountProfile(name, edit?.revision ?? read.data!.revision),
    onSuccess: async (profile) => {
      await client.cancelQueries({ queryKey: key, exact: true })
      client.setQueryData<AccountProfile>(key, (prior) => prior && prior.revision > profile.revision ? prior : profile)
      setEdit(null)
      toast("Profile saved")
    },
    onError: async (error) => {
      if (error instanceof ProfileError && error.status === 401) {
        await client.cancelQueries({ queryKey: key, exact: true })
        client.getQueryCache().find({ queryKey: key, exact: true })?.setState({ data: undefined, error, status: "error" })
      }
      toast.error(errorText(error))
    },
  })
  if (!session) return <Row title="Name" value={<NotSet />} />
  if (!read.data || (read.isError && read.error instanceof ProfileError && read.error.status === 401)) return <Row title="Name" value={read.isError ? <Button variant="outline" size="sm" onClick={() => void read.refetch()}>Retry profile</Button> : <Muted>Loading</Muted>} />
  return <Row title="Name" value={<div className="space-y-2">
    <div className="flex items-center gap-2">
      <InputGroup><InputGroupInput aria-label="Name" autoComplete="name" value={name} disabled={save.isPending} onChange={(e) => setEdit({ name: e.target.value, revision: edit?.revision ?? read.data!.revision })} /></InputGroup>
      <Button size="sm" disabled={save.isPending || !valid || name.trim() === (read.data.name ?? "")} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save"}</Button>
    </div>
    {!valid && <p className="text-xs text-destructive">Use at most 100 characters without control characters.</p>}
    {save.isError && <p role="alert" className="text-xs text-destructive">{errorText(save.error)}{save.error instanceof ProfileError && save.error.status === 409 && <Button variant="link" size="sm" onClick={() => { setEdit(null); void client.invalidateQueries({ queryKey: key, exact: true }) }}>Reload profile</Button>}</p>}
  </div>} />
}

export function Account() {
  const { theme, set } = useTheme()
  const session = useSession()
  const email = session?.email
  const role = roleIn(org().id, session)
  return (
    <>
      <Header title="Account" description="You, and how the console looks for you." />
      <Group label="Profile">
        <AccountName key={session?.email} />
        <Row title="Email" value={email ?? <NotSet />} />
        <Row title="Role" description={`Your role across ${org().name}.`} value={role ?? <Muted>Not a member</Muted>} last />
      </Group>
      <Group label="Appearance" footer="Kept in this browser.">
        <Row
          title="Theme"
          value={
            <ToggleGroup type="single" variant="outline" size="sm" value={theme} onValueChange={(v) => v && set(v as "light" | "dark")} aria-label="Theme">
              <ToggleGroupItem value="light" className="px-3 data-[state=on]:bg-accent">
                Light
              </ToggleGroupItem>
              <ToggleGroupItem value="dark" className="px-3 data-[state=on]:bg-accent">
                Dark
              </ToggleGroupItem>
            </ToggleGroup>
          }
        />
        <Row title="Time zone" description="Times show in your browser's time zone." value={Intl.DateTimeFormat().resolvedOptions().timeZone} last />
      </Group>
    </>
  )
}

const enterpriseRows = [
  { title: "Roles per project", description: "Give someone a different role in each project.", oss: "One role across the organisation." },
  { title: "Single sign-on", description: "SAML or OIDC, required for everyone, with SCIM provisioning.", oss: "Email and password." },
  { title: "Audit log", description: "Who changed a setting, created a key or opened a recording.", oss: "Not recorded." },
  { title: "More than one organisation", description: "One install serving isolated teams or clients.", oss: "One organisation." },
  { title: "License", description: "A key that turns Enterprise on, checked on this install.", oss: "No key needed." },
]

export function Organisation() {
  const [adding, setAdding] = useState(false)
  // The open-source terms are said only on a self-hosted install, never while it is unknown.
  const selfHosted = useHosted() === "self-hosted"
  return (
    <>
      <Header title={org().name} description={`The organisation that owns your projects.${selfHosted ? " The open-source edition is one organisation with any number of projects and no usage limits." : ""}`} />
      <UsageAllowance />
      <Group label="Projects">
        {org().projects.map(({ id, name: p }, k) => (
          <Row key={id} icon={TreeStructure} title={p} description={id === currentScope().project ? "Current project" : "LiveKit project"} value={<ServerUrl projectId={id} />} last={k === org().projects.length - 1} />
        ))}
      </Group>
      <div className="pt-3">
        <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
          <Plus /> Add a LiveKit project
        </Button>
      </div>
      <Group label="Enterprise" footer="A license key on this same install, or included in VoiceGateway Cloud.">
        {enterpriseRows.map((e, k) => (
          <Row key={e.title} icon={Lock} muted title={e.title} description={e.description} value={<span className="text-[12px] text-muted-foreground">Open source: {e.oss}</span>} last={k === enterpriseRows.length - 1} />
        ))}
      </Group>
      <NewProjectDialog open={adding} onOpenChange={setAdding} />
    </>
  )
}

type Role = Member["role"]

const roles: { role: Role; description: string }[] = [
  { role: "Admin", description: "Everything, including settings, projects, API keys and members." },
  { role: "Member", description: "Reads every call and changes project settings such as thresholds and tags." },
  { role: "Viewer", description: "Reads calls and traces; changes nothing." },
]

export function People() {
  const isAdmin = roleIn(org().id, useSession()) === "Admin"
  return (
    <>
      <Header title="People" description="Who can sign in, and what each person can do across the organisation." />
      {isAdmin && <Invites />}
      <MemberList isAdmin={isAdmin} />
      <Group label="Roles">
        {roles.map((r) => (
          <Row key={r.role} title={r.role} description={r.description} />
        ))}
        <Row icon={Lock} muted title="Roles per project" description="A different role in each project is VoiceGateway Enterprise." last />
      </Group>
    </>
  )
}

export const LAST_ADMIN = "An organisation needs at least one admin."

// The members from getMembers. An Admin changes roles and removes people, never themselves; the
// server refuses leaving no admin (last_admin), shown here.
function MemberList({ isAdmin }: { isAdmin: boolean }) {
  const read = useQuery(membersQuery())
  const qc = useQueryClient()
  const me = useSession()?.email
  const [problem, setProblem] = useState<string | null>(null)
  const list = read.data?.items ?? []
  const done = {
    onSuccess: (_: unknown, v: string | { userId: string }) => {
      setProblem(null)
      // A change to your own role changes what this console may show you.
      const self = list.find((m) => m.userId === (typeof v === "string" ? v : v.userId))?.email === me
      return Promise.all([qc.invalidateQueries({ queryKey: membersQuery().queryKey }), self ? refreshSession().catch(() => undefined) : undefined])
    },
    onError: (e: Error) => setProblem(e instanceof ApiError && e.code === "last_admin" ? LAST_ADMIN : errorText(e)),
  }
  const change = useMutation({ mutationFn: (v: { userId: string; role: Member["role"] }) => updateMember(v.userId, v.role), ...done })
  const remove = useMutation({ mutationFn: (userId: string) => removeMember(userId), ...done })
  // A reset link follows the invitation's rule: emailed to the member with SMTP, else shown once.
  const [resetFor, setResetFor] = useState<Outcome | null>(null)
  const reset = useMutation({
    mutationFn: (m: Member) => createPasswordReset(m.userId),
    onSuccess: (r, m) => {
      setProblem(null)
      setResetFor({ email: m.email, link: r.link, emailed: r.emailed, resend: () => reset.mutate(m) })
      return qc.invalidateQueries({ queryKey: invitationsQuery().queryKey })
    },
    onError: (e: Error) => setProblem(e instanceof ApiError && e.code === "member_of_other_organisations" ? "They also belong to an organisation you do not administer; an admin of every one of them can reset their password." : errorText(e)),
  })
  // Removing someone is not undone from here (they need a new invitation), so it asks first.
  const [removing, setRemoving] = useState<Member | null>(null)
  return (
    <Group label="Members">
      {!read.isSuccess && <Row title="Members" description={read.isError ? errorText(read.error) : undefined} value={read.isError ? "Could not be read" : "Loading"} last />}
      {list.map((m, k) => {
        const self = m.email === me
        return (
          <Row
            key={m.userId}
            title={self ? `${m.email} (you)` : m.email}
            value={
              isAdmin ? (
                <div className="flex items-center gap-2">
                  <Select value={m.role} onValueChange={(v) => change.mutate({ userId: m.userId, role: v as Member["role"] })}>
                    <SelectTrigger size="sm" className="w-28" aria-label={`Role of ${m.email}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {roles.map((r) => (
                        <SelectItem key={r.role} value={r.role}>
                          {r.role}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!self && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${m.email}`}>
                          <DotsThree />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem disabled={reset.isPending} onSelect={() => reset.mutate(m)}>
                          Password reset
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onSelect={() => setRemoving(m)}>
                          Remove
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </div>
              ) : (
                m.role
              )
            }
            last={k === list.length - 1}
          />
        )
      })}
      {problem && (
        <p role="alert" className="pt-3 text-[13px] text-destructive">
          {problem}
        </p>
      )}
      {resetFor && (
        <div className="pt-3">
          <Delivery outcome={resetFor} what="password reset link" onDone={() => setResetFor(null)} pending={reset.isPending} />
        </div>
      )}
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{`Remove ${removing?.email ?? ""}?`}</DialogTitle>
            <DialogDescription>{`They lose access to ${org().name} on their next request. Their account stays; invite them again to add them back.`}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (removing) remove.mutate(removing.userId)
                setRemoving(null)
              }}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Group>
  )
}

// What a create or a reset answered: the link without SMTP, shown once; with SMTP, whether the
// email went, and never a link.
type Outcome = { email: string; link: string | null; emailed: boolean; resend: () => void }

// The answer to one invitation or reset: copy the link once, or the email's fate with Resend,
// which is off while its request runs so repeated clicks send one email.
function Delivery({ outcome, what, onDone, pending }: { outcome: Outcome; what: string; onDone: () => void; pending: boolean }) {
  if (outcome.link)
    return (
      <div className="space-y-3">
        <p className="text-[13px] text-muted-foreground">{`Copy the ${what} for ${outcome.email} now. It is not shown again; the server keeps only its hash.`}</p>
        <CopyCommand text={outcome.link} label={`${what[0].toUpperCase()}${what.slice(1)}`} copyLabel="Copy link" />
        <Button size="sm" variant="outline" onClick={onDone}>
          Done
        </Button>
      </div>
    )
  if (outcome.emailed) return <p className="text-[13px] text-muted-foreground">{`Emailed to ${outcome.email}.`}</p>
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p role="alert" className="text-[13px] text-destructive">{`Could not send the email to ${outcome.email}.`}</p>
      <Button size="sm" variant="outline" onClick={outcome.resend} disabled={pending}>
        Resend
      </Button>
    </div>
  )
}

// The Invite form (createInvitation) and the organisation's invitations. Admins only, as the
// operations are.
function Invites() {
  const qc = useQueryClient()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<Role>("Member")
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: (v: { email: string; role: Role }) => createInvitation(v.email, v.role),
    onSuccess: (c, v) => {
      setOutcome({ email: v.email, link: c.link, emailed: c.emailed, resend: () => create.mutate(v) })
      setEmail("")
      setProblem(null)
      return qc.invalidateQueries({ queryKey: invitationsQuery().queryKey })
    },
    onError: (e: Error) => setProblem(e instanceof ApiError && e.code === "already_member" ? "That email's account is already a member." : errorText(e)),
  })
  const clean = email.trim().toLowerCase()
  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean) && !create.isPending
  return (
    <>
      <section className="pt-6">
        <form
          className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 border-b pb-6"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) create.mutate({ email: clean, role })
          }}
          aria-label="Invite people"
        >
          <div className="space-y-0.5">
            <p className="text-[14px] font-medium">Invite people</p>
            <p className="text-[13px] text-muted-foreground">{`They get a link to join ${org().name}, valid for 7 days.`}</p>
          </div>
          <div className="space-y-3">
            <InputGroup>
              <InputGroupAddon>
                <EnvelopeSimple />
              </InputGroupAddon>
              <InputGroupInput type="email" placeholder="name@company.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email to invite" />
            </InputGroup>
            <div className="flex flex-wrap items-center gap-3">
              <ToggleGroup type="single" variant="outline" size="sm" value={role} onValueChange={(v) => v && setRole(v as Role)} aria-label="Role">
                {roles.map((r) => (
                  <ToggleGroupItem key={r.role} value={r.role} className="px-3 data-[state=on]:bg-accent">
                    {r.role}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              <Button type="submit" className="ml-auto" disabled={!valid}>
                <Plus /> Send invite
              </Button>
            </div>
            {problem && (
              <p role="alert" className="text-[13px] text-destructive">
                {problem}
              </p>
            )}
            {outcome && <Delivery outcome={outcome} what="invitation link" onDone={() => setOutcome(null)} pending={create.isPending} />}
          </div>
        </form>
      </section>
      <InvitationList />
    </>
  )
}

const states: Invitation["state"][] = ["pending", "accepted", "revoked", "expired"]
const when = (iso: string) => new Date(iso).toLocaleString()

const invitationFields: FilterField<Invitation>[] = [
  { id: "state", label: "State", type: "select", icon: <CircleHalf />, options: states.map((v) => ({ value: v, label: v })), get: (i) => i.state },
  { id: "kind", label: "Kind", type: "select", icon: <Stack />, options: [{ value: "invite", label: "Invite" }, { value: "reset", label: "Password reset" }], get: (i) => i.kind },
]

// Three columns, so Revoke stays in view in the Settings pane at every width: the role, who made
// the link and when it expires sit under the email, and both wrap rather than widen the table.
const invitationColumns = (revoke: (i: Invitation) => void): Columns<Invitation> => [
  {
    id: "email",
    accessorFn: (i) => i.email,
    size: 180,
    header: ({ column }) => <DataGridColumnHeader title="Invitation" column={column} />,
    cell: ({ row }) => {
      const i = row.original
      return (
        <span className="block min-w-0 whitespace-normal">
          <span className="block break-all">{i.email}</span>
          <span className="block text-[12px] text-muted-foreground">{`${i.kind === "reset" ? "Password reset" : i.role} · expires ${when(i.expiresAt)} · by ${i.invitedBy ?? "unknown"}`}</span>
        </span>
      )
    },
  },
  { id: "state", accessorFn: (i) => i.state, size: 90, header: ({ column }) => <DataGridColumnHeader title="State" column={column} />, cell: ({ row }) => row.original.state },
  {
    id: "actions",
    size: 76,
    enableSorting: false,
    header: () => null,
    cell: ({ row }) =>
      row.original.state === "pending" && (
        <Button variant="ghost" size="sm" onClick={() => revoke(row.original)} aria-label={`Revoke ${row.original.email}`}>
          Revoke
        </Button>
      ),
  },
]

// The organisation's invitations and reset links (getInvitations), newest first as the server
// sends them; the state filter and the sort are in the URL. Revoke asks first.
function InvitationList() {
  const read = useQuery(invitationsQuery())
  const qc = useQueryClient()
  const [revoking, setRevoking] = useState<Invitation | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const revoke = useMutation({
    mutationFn: (id: string) => revokeInvitation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: invitationsQuery().queryKey }),
    onError: (e: Error) => setProblem(errorText(e)),
  })
  const columns = invitationColumns(setRevoking)
  return (
    <Group label="Invitations">
      {!read.isSuccess && <Row title="Invitations" description={read.isError ? errorText(read.error) : undefined} value={read.isError ? "Could not be read" : "Loading"} last />}
      {read.isSuccess && (
        <div className="pt-2">
          <DataTable data={read.data.items} columns={columns} fields={invitationFields} getRowId={(i) => i.id} phoneColumns={["email", "state", "actions"]} urlPrefix="invites." empty="No invitations match these filters." />
        </div>
      )}
      {problem && (
        <p role="alert" className="pt-3 text-[13px] text-destructive">
          {problem}
        </p>
      )}
      <Dialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{`Revoke the ${revoking?.kind === "reset" ? "reset link" : "invitation"} for ${revoking?.email ?? ""}?`}</DialogTitle>
            <DialogDescription>{revoking?.kind === "reset" ? "The link stops working at once. Make a new reset link if they still need one." : "The link stops working at once. Invite them again for a new one."}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (revoking) revoke.mutate(revoking.id)
                setRevoking(null)
              }}
            >
              Revoke
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Group>
  )
}

type Integration = { logo: string; name: string; description: string; status: string; ok?: boolean; body?: React.ReactNode; icon?: Icon; connect?: boolean }

function IntegrationList({ list }: { list: Integration[] }) {
  return (
    <ItemGroup className="gap-0 has-data-[size=sm]:gap-0">
      {list.map((x, k) => (
        <span key={x.name} className="contents">
          <Item role="listitem" size="sm" className="flex-col items-stretch gap-3 rounded-none border-0 px-0 py-4">
            <div className="flex items-center gap-4">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border bg-background">{x.icon ? <x.icon size={20} /> : <ProviderLogo provider={x.logo} size={20} />}</span>
              <ItemContent className="min-w-0 gap-0.5">
                <ItemTitle className="text-[14px] font-medium">{x.name}</ItemTitle>
                <ItemDescription className={x.ok ? "truncate font-mono text-[12px]" : "line-clamp-none text-[13px]"}>{x.description}</ItemDescription>
              </ItemContent>
              {x.connect ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span tabIndex={0}>
                      <Button variant="outline" size="sm" disabled>
                        Connect
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-60 text-xs">Connects on the server, so the webhook URL or key never sits in the browser.</TooltipContent>
                </Tooltip>
              ) : (
                <Badge variant={x.ok ? "secondary" : "outline"} className="shrink-0 font-normal text-muted-foreground">
                  {x.status}
                </Badge>
              )}
            </div>
            {x.body && <div className="pl-14">{x.body}</div>}
          </Item>
          {k < list.length - 1 && <ItemSeparator className="my-0" />}
        </span>
      ))}
    </ItemGroup>
  )
}

const notifications: Integration[] = [
  { logo: "slack", name: "Slack", description: "Post alerts to a channel.", status: "", connect: true },
  { logo: "teams", name: "Microsoft Teams", description: "Post alerts to a channel.", status: "", connect: true },
  { logo: "discord", name: "Discord", description: "Post alerts to a channel.", status: "", connect: true },
  { logo: "pagerduty", name: "PagerDuty", description: "Page whoever is on call for high alerts.", status: "", connect: true },
  { logo: "opsgenie", name: "Opsgenie", description: "Page whoever is on call for high alerts.", status: "", connect: true },
  { logo: "", icon: Envelope, name: "Email", description: "Send alerts to an address or a list.", status: "", connect: true },
  { logo: "", icon: WebhooksLogo, name: "Webhook", description: "POST each alert as JSON to your endpoint.", status: "", connect: true },
]
// The LiveKit project this project reads (getProjectConnection's livekit): its URL, which never
// changes, and the API key agents and webhooks sign with. An Admin changes the key and secret here.
function LiveKitProject() {
  const read = useQuery(connectionQuery())
  const isAdmin = roleIn(org().id, useSession()) === "Admin"
  const lk = read.data?.livekit
  const pending = <Muted>{read.isError ? "Could not be read" : "Loading"}</Muted>
  return (
    <>
      <Group label="LiveKit project" footer={isAdmin ? undefined : "Only an Admin changes the key and secret."}>
        <Row title="LiveKit URL" description="Another LiveKit project is another VoiceGateway project." value={lk ? <span className="font-mono text-[13px] break-all">{lk.url}</span> : pending} />
        <Row title="API key" description="Agents and LiveKit webhooks sign with this key." value={lk ? <span className="font-mono text-[13px] break-all">{lk.apiKey}</span> : pending} last />
      </Group>
      {isAdmin && lk && (
        <section className="pt-6">
          <h3 className="pb-3 text-[14px] font-semibold">Change key and secret</h3>
          <ChangeLiveKitKey url={lk.url} />
        </section>
      )}
    </>
  )
}

export function Connection() {
  return (
    <>
      <Header title="Connection" description="The LiveKit project this project reads, the URLs your agent and LiveKit send to, and the last outcome of each." />
      <LiveKitProject />
      <div className="pt-8">
        <ConnectAgentPanel framed={false} />
      </div>
    </>
  )
}

function Integrations() {
  return (
    <>
      <Header title="Integrations" description="Where this project's alerts go. Pick channels per rule on the Alerts page." />
      <div className="pt-2">
        <IntegrationList list={notifications} />
      </div>
    </>
  )
}

const mcpClients = [
  { key: "claude", name: "Claude Code", logo: "claudecode", command: 'claude mcp add --transport http voicegateway "$VOICEGATEWAY_MCP_URL" \\\n  --header "Authorization: Bearer $VOICEGATEWAY_API_KEY"' },
  { key: "codex", name: "Codex", logo: "codex", command: 'codex mcp add voicegateway --url "$VOICEGATEWAY_MCP_URL" \\\n  --bearer-token-env-var VOICEGATEWAY_API_KEY' },
  { key: "cursor", name: "Cursor", logo: "cursor", command: '{ "mcpServers": { "voicegateway": {\n  "url": "${env:VOICEGATEWAY_MCP_URL}",\n  "headers": { "Authorization": "Bearer ${env:VOICEGATEWAY_API_KEY}" } } } }' },
]

export function ApiKeys() {
  const isAdmin = roleIn(org().id, useSession()) === "Admin"
  return (
    <>
      <Header title="API keys" description="For scripts and MCP. Each key reads one project, and changes nothing." />
      {!isAdmin ? (
        <Group>
          <Row icon={KeyIcon} title="Managed by admins" description="An admin of this organisation creates and revokes API keys." last />
        </Group>
      ) : (
        org().projects.map((p) => <ProjectKeyList key={p.id} projectId={p.id} name={p.name} />)
      )}
      <Group label="Enterprise">
        <Row icon={Lock} muted title="Ingestion limits per key" description="Cap what one key or one project can send." value={<span className="text-[12px] text-muted-foreground">Open source: no limits.</span>} last />
      </Group>
    </>
  )
}

// One project's keys (listProjectKeys), with Create, which shows the new key once, and Revoke,
// which asks first.
function ProjectKeyList({ projectId, name }: { projectId: string; name: string }) {
  const read = useQuery(projectKeysQuery(projectId))
  const qc = useQueryClient()
  const [naming, setNaming] = useState(false)
  const [keyName, setKeyName] = useState("")
  const [shown, setShown] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<ProjectKey | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: projectKeysQuery(projectId).queryKey })
  const create = useMutation({
    mutationFn: () => createProjectKey(projectId, keyName.trim()),
    onSuccess: (k) => {
      setShown(k.secret)
      setKeyName("")
      setProblem(null)
      return refresh()
    },
    onError: (e: Error) => setProblem(errorText(e)),
  })
  const revoke = useMutation({ mutationFn: (keyId: string) => revokeProjectKey(projectId, keyId), onSuccess: () => refresh(), onError: (e: Error) => setProblem(errorText(e)) })
  const list = read.data?.items ?? []
  const state = read.isError ? (read.error instanceof ApiError && read.error.status === 404 ? "Not served by this server yet" : errorText(read.error)) : "Loading"
  return (
    <Group
      label={name}
      footer={
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setProblem(null)
            setNaming(true)
          }}
          disabled={!read.isSuccess}
        >
          <Plus /> Create key
        </Button>
      }
    >
      {!read.isSuccess && <Row icon={KeyIcon} title="Keys" value={<Muted>{state}</Muted>} last />}
      {read.isSuccess && list.length === 0 && <Row icon={KeyIcon} title="No keys yet" last />}
      {list.map((k, n) => (
        <Row
          key={k.keyId}
          icon={KeyIcon}
          muted={k.revokedAt !== null}
          title={k.name}
          description={`${k.prefix} · ${k.revokedAt ? "revoked" : k.lastUsedAt ? `last used ${new Date(k.lastUsedAt).toLocaleString()}` : "never used"}${k.createdBy ? ` · by ${k.createdBy}` : ""}`}
          value={
            k.revokedAt === null && (
              <Button variant="ghost" size="sm" onClick={() => setRevoking(k)} aria-label={`Revoke ${k.name}`}>
                Revoke
              </Button>
            )
          }
          last={n === list.length - 1}
        />
      ))}
      {problem && (
        <p role="alert" className="pt-3 text-[13px] text-destructive">
          {problem}
        </p>
      )}
      <Dialog
        open={naming || shown !== null}
        onOpenChange={(o) => {
          if (o) return
          setNaming(false)
          setShown(null)
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{shown ? "Your new API key" : `New API key for ${name}`}</DialogTitle>
            <DialogDescription>{shown ? "Copy it now. It is not shown again; the server keeps only its hash." : "It reads this project only and changes nothing."}</DialogDescription>
          </DialogHeader>
          {shown ? (
            <CopyCommand text={shown} label="New API key" />
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (keyName.trim()) create.mutate()
              }}
            >
              <InputGroup>
                <InputGroupAddon>
                  <KeyIcon />
                </InputGroupAddon>
                <InputGroupInput placeholder="ci" maxLength={64} value={keyName} onChange={(e) => setKeyName(e.target.value)} aria-label="Key name" />
              </InputGroup>
              {problem && (
                <p role="alert" className="pt-3 text-[13px] text-destructive">
                  {problem}
                </p>
              )}
              <DialogFooter className="pt-4">
                <Button type="submit" disabled={!keyName.trim() || create.isPending}>
                  Create key
                </Button>
              </DialogFooter>
            </form>
          )}
          {shown && (
            <DialogFooter>
              <Button
                onClick={() => {
                  setShown(null)
                  setNaming(false)
                }}
              >
                Done
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>
      <Dialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{`Revoke ${revoking?.name ?? ""}?`}</DialogTitle>
            <DialogDescription>{`Anything using ${revoking?.prefix ?? "it"} gets 401 from its next request. This cannot be undone.`}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (revoking) revoke.mutate(revoking.keyId)
                setRevoking(null)
              }}
            >
              Revoke
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Group>
  )
}

function Mcp() {
  return (
    <>
      <Header title="MCP server" description="Ask about calls and traces from Claude Code, Codex or any coding agent." />
      <Group label="Server" footer="Served by VoiceGateway itself, over streamable HTTP, once the Phase 2 server runs.">
        <Row title="URL" description="Shown here once the server runs." value={<NotSet />} last />
      </Group>
      <section className="pt-6">
        <h3 className="pb-1 text-[14px] font-semibold">Connect</h3>
        <p className="pb-3 text-[13px] text-muted-foreground">Set VOICEGATEWAY_MCP_URL and VOICEGATEWAY_API_KEY first, then run this once in your project.</p>
        <Tabs defaultValue="claude" className="gap-3">
          <TabsList variant="line">
            {mcpClients.map((c) => (
              <TabsTrigger key={c.key} value={c.key} className="gap-1.5">
                <ProviderLogo provider={c.logo} size={14} />
                {c.name}
              </TabsTrigger>
            ))}
          </TabsList>
          {mcpClients.map((c) => (
            <TabsContent key={c.key} value={c.key}>
              <CopyCommand text={c.command} label={`${c.name} MCP command`} />
            </TabsContent>
          ))}
        </Tabs>
      </section>
    </>
  )
}

function AuditLog() {
  // The open-source terms are said only on a self-hosted install.
  const selfHosted = useHosted() === "self-hosted"
  return (
    <>
      <Header title="Audit log" description="Every change, who made it and when. Nobody can edit it." badge="Enterprise" />
      <div className="mt-6 flex flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 text-center">
        <Lock size={20} className="text-muted-foreground" />
        <p className="text-[14px] font-medium">Part of VoiceGateway Enterprise</p>
        <p className="max-w-[46ch] text-[13px] text-muted-foreground">{`Who changed a setting, created a key or opened a recording.${selfHosted ? " The open-source edition does not record changes (decision 0017)." : ""}`}</p>
      </div>
    </>
  )
}

const accessTabs = [
  { key: "people", label: "People", body: People },
  { key: "keys", label: "API keys", body: ApiKeys },
  { key: "mcp", label: "MCP server", body: Mcp },
  { key: "audit", label: "Audit log", body: AuditLog },
]
function Access() {
  const [tab, setTab] = useUrlState("access", "people")
  return (
    <Tabs value={tab} onValueChange={setTab} className="gap-0">
      <TabsList variant="line" className="-mx-4 -mt-6 mb-6 h-12 w-[calc(100%+2rem)] justify-start overflow-x-auto rounded-none border-b px-2 sm:-mx-8 sm:w-[calc(100%+4rem)] sm:px-6">
        {accessTabs.map((t) => (
          <TabsTrigger key={t.key} value={t.key} className="flex-none px-2">
            {t.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {accessTabs.map((t) => (
        <TabsContent key={t.key} value={t.key}>
          <t.body />
        </TabsContent>
      ))}
    </Tabs>
  )
}

const days = (n: number | null) => (n === null ? <NotSet /> : `${n} ${n === 1 ? "day" : "days"}`)

const Data = () => <WithSettings title="Data">{(s) => <DataPane s={s} />}</WithSettings>

// How many calls have a recording, from listCalls' totals over every call. A count the server could
// not give stays unknown.
function RecordedCalls() {
  const read = useQuery(callTotalsQuery(new URLSearchParams()))
  if (read.isPending) return <Muted>Loading</Muted>
  if (read.isError) return <Muted>Could not be read</Muted>
  const { recorded, callCount } = read.data
  return `${recorded ?? "Unknown"} of ${callCount ?? "unknown"}`
}

function DataPane({ s }: { s: ProjectSettings }) {
  return (
    <>
      <Header title="Data" description="Recordings and how long this project keeps what it collects. It all stays in your own storage." badge="Read only" />
      <Group label="Recordings">
        <Row title="Captured calls with a recording" value={<RecordedCalls />} />
        <Row title="Source" description="Turned on in the agent session. LiveKit Egress is the other option." value="Agent session upload" />
        <Row title="Storage bucket" description="SeaweedFS from the install, or any S3 bucket." value={s.storageBucket ?? <NotSet />} last />
      </Group>
      <Group label="Retention" footer="The server deletes what is older. Until it runs, nothing is deleted.">
        <Row title="Transcripts" value={days(s.transcriptRetentionDays)} />
        <Row title="Raw traces and metrics" value={days(s.traceRetentionDays)} />
        <Row title="Recordings" value={days(s.recordingRetentionDays)} last />
      </Group>
    </>
  )
}

// A settings pane whose values come from getProjectSettings: loading, the error, then the pane.
function WithSettings({ title, children }: { title: string; children: (s: ProjectSettings) => React.ReactNode }) {
  const read = useQuery(projectSettingsQuery())
  if (read.data) return children(read.data)
  return (
    <>
      <Header title={title} description={read.isError ? `The project's settings could not be read. ${errorText(read.error)}` : "Loading the project's settings."} />
      {read.isError && (
        <Button variant="outline" size="sm" className="mt-4" onClick={() => void read.refetch()}>
          Try again
        </Button>
      )}
    </>
  )
}

export const Thresholds = () => <WithSettings title="Thresholds">{(s) => <ThresholdsForm key={`${currentScope().org}/${currentScope().project}`} settings={s} />}</WithSettings>

function ThresholdsForm({ settings }: { settings: ProjectSettings }) {
  const t = settings.thresholds
  const selected = currentScope()
  const qc = useQueryClient()
  const role = roleIn(selected.org, useSession())
  const canEdit = role === "Member" || role === "Admin"
  const asDraft = (x: T) => ({ answer: String(x.answerMs / 1000), join: String(x.joinMs / 1000), tts: String(x.ttsStartMs / 1000) })
  const [edit, setEdit] = useState<{ revision: number; values: ReturnType<typeof asDraft> } | null>(null)
  const draft = edit?.values ?? asDraft(t)
  const n = (v: string) => Number(v)
  const valid = [draft.answer, draft.join, draft.tts].every((v) => v.trim() !== "" && Number.isFinite(n(v)) && n(v) >= 0.1 && n(v) <= 60)
  // Shift the decimal exponent before parsing, avoiding binary multiplication drift.
  // Unlike rounding to whole milliseconds, this retains fractional thresholds.
  const ms = (v: string) => {
    const [significand, exponent = "0"] = n(v).toString().split("e")
    return Number(`${significand}e${Number(exponent) + 3}`)
  }
  const changed = ms(draft.answer) !== t.answerMs || ms(draft.join) !== t.joinMs || ms(draft.tts) !== t.ttsStartMs
  const save = useMutation({
    mutationFn: (v: { thresholds: T; revision: number; message: string; undo?: T }) => saveProjectSettings(selected, { thresholds: v.thresholds, revision: v.revision }),
    onSuccess: async (saved, variables) => {
      setEdit(null)
      await qc.cancelQueries({ queryKey: ["settings", selected], exact: true })
      qc.setQueryData<ProjectSettings>(["settings", selected], (prior) => prior && prior.revision > saved.revision ? prior : saved)
      await qc.invalidateQueries({ predicate: (query) => {
        const scope = query.queryKey[1] as { org?: string; project?: string } | undefined
        return query.queryKey[0] !== "settings" && scope?.org === selected.org && scope?.project === selected.project
      } })
      toast(variables.message, variables.undo ? { action: { label: "Undo", onClick: () => save.mutate({ thresholds: variables.undo!, revision: saved.revision, message: "Thresholds restored" }) } } : undefined)
    },
    onError: async (error) => {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        await qc.cancelQueries({ queryKey: ["settings", selected], exact: true })
        qc.getQueryCache().find({ queryKey: ["settings", selected], exact: true })?.setState({ data: undefined, dataUpdatedAt: 0, error, status: "error" })
        void refreshSession().catch(() => toast.error("Could not refresh your session."))
      }
      toast.error(errorText(error))
    },
  })
  const apply = (next: T, message: string) => save.mutate({ thresholds: next, revision: edit?.revision ?? settings.revision, message, undo: t })
  const field = (key: keyof typeof draft, label: string, hint: string) => (
    <Field orientation="horizontal" className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 py-4">
      <div className="space-y-0.5">
        <FieldLabel htmlFor={`th-${key}`} className="text-[14px]">
          {label}
        </FieldLabel>
        <FieldDescription className="text-[13px]">{hint}</FieldDescription>
      </div>
      <InputGroup className="w-28">
        <InputGroupInput id={`th-${key}`} disabled={!canEdit || save.isPending} type="number" min={0.1} max={60} step={0.1} value={draft[key]} onChange={(e) => setEdit({ revision: edit?.revision ?? settings.revision, values: { ...draft, [key]: e.target.value } })} className="tnum" />
        <InputGroupAddon align="inline-end">s</InputGroupAddon>
      </InputGroup>
    </Field>
  )
  return (
    <>
      <Header title="Thresholds" description="The numbers this project's checks judge calls against. Settings, not measurements; every page uses the same ones." badge="Project" />
      <FieldGroup className="gap-0 pt-2 [&>[data-slot=field]+[data-slot=field]]:border-t">
        {field("answer", "Answer time", "Caller finishing to the agent's first audio.")}
        {field("join", "Agent join", "Dispatch to the agent joining the room.")}
        {field("tts", "Speech start", "Text to speech's time to first audio.")}
      </FieldGroup>
      {!canEdit && <p className="py-3 text-[13px] text-muted-foreground">A Member or Admin can change project thresholds.</p>}
      {save.isError && <p role="alert" className="py-3 text-[13px] text-destructive">
        {errorText(save.error)}
        {save.error instanceof ApiError && save.error.status === 409 && <Button variant="link" onClick={() => { setEdit(null); void qc.invalidateQueries({ queryKey: ["settings", selected], exact: true }) }}>Reload settings</Button>}
      </p>}
      <div className="flex items-center justify-end gap-3 border-t pt-5">
        {!valid && <span className="mr-auto text-[13px] text-destructive">Use a number between 0.1 and 60.</span>}
        <Button variant="outline" disabled={!canEdit || save.isPending} onClick={() => apply(DEFAULTS, "Thresholds reset to defaults")}>
          Reset to defaults
        </Button>
        <Button disabled={!canEdit || save.isPending || !valid || !changed} onClick={() => apply({ answerMs: ms(draft.answer), joinMs: ms(draft.join), ttsStartMs: ms(draft.tts) }, "Thresholds saved")}>
          {save.isPending ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </>
  )
}

// Widgets a platform puts in its own product (decisions 0023, 0034). The preview is the real widget
// in a frame of its real size, read with this session; on another site it reads with an embed token.
function Embeds() {
  const [pick, setPick] = useUrlState<WidgetId>("widget", "answer-time")
  const w = widgets.find((x) => x.id === pick) ?? widgets[0]
  const [sizePick, setSize] = useUrlState<string>("size", w.sizes[0])
  const size: Size = (w.sizes as readonly string[]).includes(sizePick) ? (sizePick as Size) : w.sizes[0]
  const [theme, setTheme] = useState<"light" | "dark">("light")
  const frame = sizes[size]
  // The call widget previews the newest call with a turn; other widgets need no call.
  const latest = useQuery({ ...latestMatchingCallQuery(new URLSearchParams({ turnsMin: "1" })), enabled: pick === "call" })
  const callId = latest.data?.id
  const src = `${window.location.origin}${scopePrefix()}/embed/${w.id}`
  const query = (token: string) => `?token=${token}&theme=${theme}${w.sizes.length > 1 ? `&size=${size}` : ""}${w.id === "call" ? "&call=CALL_ID" : ""}`
  const preview = `${src}?theme=${theme}${w.sizes.length > 1 ? `&size=${size}` : ""}${w.id === "call" && callId ? `&call=${encodeURIComponent(callId)}` : ""}`
  const snippet = `<iframe\n  src="${src}${query("EMBED_TOKEN")}"\n  width="${frame.width}"\n  height="${frame.height}"\n  style="border: 0; border-radius: 22px"\n  title="${w.title}, VoiceGateway"\n></iframe>`
  const grant = [w.id, ...w.with]
  // Transcripts and audio reach the other site only when asked for (decision 0034).
  const [contentPick, setContent] = useUrlState<string>("content", "off")
  const hasCall = grant.includes("call")
  const content = hasCall && contentPick === "on"
  const mint = `curl -X POST ${window.location.origin}/api/v1${scopePrefix()}/embed-tokens \\\n  -H "Authorization: Bearer $VOICEGATEWAY_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"widgets": ${JSON.stringify(grant)}, "origin": "https://your-app.example.com"${content ? `, "transcriptsAndAudio": true` : ""}}'`
  return (
    <>
      <Header title="Embeds" description="Put this project's widgets in your own product. Each one is a small frame behind a short-lived, read-only token, so your API key never reaches the page." />
      <section className="space-y-3 pt-6">
        <div className="flex flex-wrap items-center gap-3">
          <Select value={w.id} onValueChange={(v) => setPick(v as WidgetId)}>
            <SelectTrigger size="sm" className="w-48" aria-label="Widget">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {widgets.map((x) => (
                <SelectItem key={x.id} value={x.id}>
                  {x.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {w.sizes.length > 1 && (
            <ToggleGroup type="single" variant="outline" size="sm" value={size} onValueChange={(v) => v && setSize(v)} aria-label="Widget size">
              {w.sizes.map((x) => (
                <ToggleGroupItem key={x} value={x} className="px-3 capitalize data-[state=on]:bg-accent">
                  {x}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
          <ToggleGroup type="single" variant="outline" size="sm" value={theme} onValueChange={(v) => v && setTheme(v as "light" | "dark")} aria-label="Widget theme">
            <ToggleGroupItem value="light" className="px-3 data-[state=on]:bg-accent">
              Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark" className="px-3 data-[state=on]:bg-accent">
              Dark
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
        <p className="text-[13px] text-muted-foreground">{w.question}</p>
        {w.id === "call" && !callId ? (
          <p className="rounded-xl border px-4 py-10 text-center text-[13px] text-muted-foreground">
            {latest.isPending ? "Loading a call for the preview." : latest.isError ? `A call for the preview could not be read. ${errorText(latest.error)}` : "No call with a turn yet, so there is nothing to preview."}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-2xl bg-muted/50 p-6">
            <iframe key={preview} src={preview} title={`${w.title} preview`} className="mx-auto block max-w-full rounded-[22px] shadow-sm" width={frame.width} height={Math.min(frame.height, 520)} />
          </div>
        )}
      </section>
      <section className="pt-6">
        <h3 className="pb-1 text-[14px] font-semibold">1. Your backend asks for a token</h3>
        <p className="pb-3 text-[13px] text-muted-foreground">With a project API key, for the widgets the frame shows and the site that shows them. A token lasts an hour unless you pass expiresInSeconds (1 minute to 1 day).</p>
        {hasCall && (
          <Group label="What the call view shares">
            <Row
              title="Share transcripts and audio"
              description="Off: the one-call view shows timings, models and issues only. On: the other site sees what callers and the agent said, prompts and tool arguments, and can play the recording. Needs an Admin or a project key."
              last
              value={<Switch checked={content} onCheckedChange={(on) => setContent(on ? "on" : "off")} aria-label="Share transcripts and audio" />}
            />
          </Group>
        )}
        <CopyCommand text={mint} label="Token request" />
      </section>
      <section className="pt-6">
        <h3 className="pb-1 text-[14px] font-semibold">2. Your page shows the frame</h3>
        <p className="pb-3 text-[13px] text-muted-foreground">Put the token in place of EMBED_TOKEN.</p>
        <CopyCommand text={snippet} label="Embed snippet" />
      </section>
      <AllowedOrigins />
      <TestToken key={`${grant.join(",")}|${content}`} grant={grant} content={content} query={query} src={src} />
      <Group label="Enterprise">
        <Row icon={Lock} muted title="Per-customer tokens" description="A token that shows only one of your customers' calls." />
        <Row icon={Lock} muted title="Remove VoiceGateway branding" description="Widgets without Powered by VoiceGateway." value={<span className="text-[12px] text-muted-foreground">Open source: always shown.</span>} last />
      </Group>
    </>
  )
}

// The sites allowed to show the widgets. Admins change them; removing a site stops its tokens at
// their next request.
function AllowedOrigins() {
  const client = useQueryClient()
  const read = useQuery(projectSettingsQuery())
  const isAdmin = roleIn(org().id, useSession()) === "Admin"
  const [draft, setDraft] = useState("")
  const save = useMutation({
    mutationFn: (origins: string[]) => saveAllowedOrigins(currentScope(), origins, read.data!.allowedOriginsRevision),
    onSuccess: (settings) => {
      client.setQueryData(projectSettingsQuery().queryKey, settings)
      setDraft("")
      toast("Allowed sites saved")
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void client.invalidateQueries({ queryKey: projectSettingsQuery().queryKey })
      toast.error(errorText(error))
    },
  })
  const origins = read.data?.allowedOrigins ?? []
  return (
    <Group label="Allowed sites" footer={isAdmin ? "An origin is https://host or https://host:port, with no path. http works only for localhost." : "Only an Admin changes the allowed sites."}>
      {!read.data ? (
        <Row title="Sites" value={read.isError ? "Could not be read" : "Loading"} last />
      ) : (
        <>
          {origins.length === 0 && <Row title="No site yet" description="Until you add one, no token can be made and no site can show the widgets." />}
          {origins.map((o) => (
            <Row
              key={o}
              title={o}
              value={
                isAdmin && (
                  <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => save.mutate(origins.filter((x) => x !== o))} aria-label={`Remove ${o}`}>
                    <X /> Remove
                  </Button>
                )
              }
            />
          ))}
          {isAdmin && (
            <Row
              title="Add a site"
              last
              value={
                <form
                  className="flex w-full items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (draft.trim()) save.mutate([...origins, draft.trim()])
                  }}
                >
                  <InputGroup>
                    <InputGroupInput aria-label="Site origin" placeholder="https://app.example.com" value={draft} disabled={save.isPending} onChange={(e) => setDraft(e.target.value)} />
                  </InputGroup>
                  <Button size="sm" type="submit" disabled={save.isPending || !draft.trim()}>
                    <Plus /> Add
                  </Button>
                </form>
              }
            />
          )}
        </>
      )}
    </Group>
  )
}

// A token made here, for trying the snippet on an allowed site. It is shown once and never stored.
function TestToken({ grant, content, query, src }: { grant: string[]; content: boolean; query: (token: string) => string; src: string }) {
  const read = useQuery(projectSettingsQuery())
  const origins = read.data?.allowedOrigins ?? []
  const [origin, setOrigin] = useState<string | null>(null)
  const chosen = origin && origins.includes(origin) ? origin : (origins[0] ?? null)
  const mint = useMutation({ mutationFn: () => createEmbedToken({ widgets: grant as CreateEmbedToken["widgets"], origin: chosen!, expiresInSeconds: 3600, transcriptsAndAudio: content }), onError: (error) => toast.error(errorText(error)) })
  return (
    <Group label="Try it" footer={mint.data ? `For ${mint.data.origin}, until ${new Date(mint.data.expiresAt).toLocaleTimeString()}. It opens ${mint.data.widgets.join(" and ")}${mint.data.transcriptsAndAudio ? ", with transcripts and audio" : ", without transcripts or audio"}.` : "A test token for one hour, made with your sign-in. Your product's backend makes its own with an API key."}>
      <Row
        title="Test token"
        last
        value={
          !origins.length ? (
            <Muted>Add an allowed site first.</Muted>
          ) : mint.data ? (
            <div className="w-full min-w-0">
              <CopyCommand text={`${src}${query(mint.data.token)}`} label="Widget address with a test token" />
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Select value={chosen ?? undefined} onValueChange={setOrigin}>
                <SelectTrigger size="sm" className="w-64" aria-label="Site for the token">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {origins.map((o) => (
                    <SelectItem key={o} value={o}>
                      {o}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" disabled={mint.isPending || !chosen} onClick={() => mint.mutate()}>
                <Plus /> {mint.isPending ? "Making…" : "Make a test token"}
              </Button>
            </div>
          )
        }
      />
    </Group>
  )
}

const body: Record<Key, () => React.ReactNode> = { account: Account, organisation: Organisation, access: Access, connection: Connection, integrations: Integrations, embeds: Embeds, data: Data, thresholds: Thresholds }

export type SettingsPage = Key
export function SettingsDialog({ open, onOpenChange, page, onPageChange, orgOnly = false }: { open: boolean; onOpenChange: (o: boolean) => void; page: string | null; onPageChange: (p: Key) => void; orgOnly?: boolean }) {
  // With no project to show, only the organisation's pages and yours: People among them, so an admin
  // can manage members before the first project is added.
  const shown = orgOnly ? groups.filter((g) => g.scope !== "project") : groups
  const shownPages = shown.flatMap((g) => g.pages)
  // The page lives in the URL (?settings=thresholds); an unknown value shows the first page.
  const active: Key = shownPages.some((p) => p.key === page) ? (page as Key) : shownPages[0].key
  const setActive = onPageChange
  const current = shownPages.find((p) => p.key === active)!
  // The picked project follows the URL: after a project switch the picker starts on the new one.
  const scope = currentScope()
  const picked = scope.project
  const pickedName = org().projects.find((p) => p.id === picked)?.name ?? picked
  const setPicked = (id: string) => {
    if (id === scope.project || !org().projects.some((p) => p.id === id)) return
    // A new document resets all project-bound drafts and query observers together.
    window.location.assign(`/org/${encodeURIComponent(scope.org)}/project/${encodeURIComponent(id)}/overview?settings=${active}`)
  }
  const Body = body[active]
  const nav = (p: Page) => (
    <SidebarMenuItem key={p.key}>
      <SidebarMenuButton
        isActive={p.key === active}
        onClick={() => setActive(p.key)}
        // Ink, not blue, for hover and the current page: blue stays rare in the console.
        className="h-9 rounded-md text-[14px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground data-[active=true]:bg-muted data-[active=true]:text-foreground"
      >
        <p.icon />
        <span>{p.label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="grid-cols-[minmax(0,1fr)] gap-0 overflow-hidden rounded-2xl p-0 shadow-2xl sm:max-w-[920px]"
        // No focus ring on the first item when the modal opens; Tab still reaches everything.
        onOpenAutoFocus={(e) => e.preventDefault()}
        // A toast's Undo sits outside the modal; clicking it must not close Settings.
        onPointerDownOutside={(e) => (e.detail.originalEvent.target as Element | null)?.closest?.("[data-sonner-toaster]") && e.preventDefault()}
      >
        <header className="flex items-start gap-4 border-b px-4 pt-5 pb-4 sm:px-6 sm:pt-6 sm:pb-5">
          <span className="hidden size-12 shrink-0 items-center justify-center rounded-xl border bg-background shadow-xs sm:flex">
            <GearSix size={22} />
          </span>
          <div className="min-w-0 flex-1 space-y-0.5">
            <DialogTitle className="text-[18px] font-semibold">Settings</DialogTitle>
            <DialogDescription className="text-[14px]">
              {current.scope === "you" ? "Your own account." : current.scope === "organisation" ? `Settings for ${org().name}.` : `Settings for ${pickedName}.`}{" "}
              <span className="whitespace-nowrap">
                Open anywhere with{" "}
                <KbdGroup>
                  <Kbd>{mod}</Kbd>
                  <Kbd>,</Kbd>
                </KbdGroup>
              </span>
            </DialogDescription>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={() => onOpenChange(false)} aria-label="Close settings" className="-mt-1 -mr-2 text-muted-foreground">
            <X />
          </Button>
        </header>
        <SidebarProvider className="min-h-0 items-start">
          <Sidebar collapsible="none" className="hidden h-[580px] w-56 border-r bg-background md:flex">
            <SidebarContent className="gap-0 px-2 py-4">
              {shown.map((g, k) => (
                <SidebarGroup key={g.label} className={k ? "pt-5 pb-0" : "py-0"}>
                  <SidebarGroupLabel className="px-2 text-[12px] font-semibold text-muted-foreground">{g.label}</SidebarGroupLabel>
                  {g.scope === "project" && (
                    <Select value={picked} onValueChange={setPicked}>
                      <SelectTrigger size="sm" className="mb-1.5 w-full" aria-label="Project">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {org().projects.map(({ id, name: p }) => (
                          <SelectItem key={id} value={id}>
                            {p}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  <SidebarGroupContent>
                    <SidebarMenu className="gap-0.5">{g.pages.map(nav)}</SidebarMenu>
                  </SidebarGroupContent>
                </SidebarGroup>
              ))}
            </SidebarContent>
          </Sidebar>
          <main className="flex h-[min(580px,72svh)] min-w-0 flex-1 flex-col overflow-hidden">
            {/* Phones and tablets: one picker in place of the side menu. */}
            <div className="grid auto-cols-fr grid-flow-col gap-2 border-b px-4 py-3 md:hidden">
              <Select value={active} onValueChange={(v) => setActive(v as Key)}>
                <SelectTrigger size="sm" className="w-full min-w-0" aria-label="Settings page">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {shown.map((g) => (
                    <SelectGroup key={g.label}>
                      <SelectLabel>{g.label}</SelectLabel>
                      {g.pages.map((p) => (
                        <SelectItem key={p.key} value={p.key}>
                          {p.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
              {current.scope === "project" && (
                <Select value={picked} onValueChange={setPicked}>
                  <SelectTrigger size="sm" className="w-full min-w-0" aria-label="Project">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {org().projects.map(({ id, name: p }) => (
                      <SelectItem key={id} value={id}>
                        {p}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            {/* The page scrolls here; it takes focus so a keyboard can scroll a page with no control in it. */}
            <div role="region" aria-label={current.label} tabIndex={0} className="flex-1 overflow-y-auto px-4 pt-6 pb-10 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset sm:px-8">
              <Body />
            </div>
          </main>
        </SidebarProvider>
      </DialogContent>
    </Dialog>
  )
}
