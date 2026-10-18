import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { EnvelopeSimple as Envelope, WebhooksLogo, Buildings, Database, Key as KeyIcon, Clock, Copy, EnvelopeSimple, GearSix, FrameCorners, Lock, UsersThree, X, Plug, Plus, TreeStructure, User } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ProviderLogo } from "@/components/provider-logo"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemSeparator, ItemTitle } from "@/components/ui/item"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, SelectGroup, SelectLabel } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider } from "@/components/ui/sidebar"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useTheme } from "@/components/theme"
import { NewProjectDialog } from "@/components/new-project-dialog"
import { currentOrg, currentScope, scopePrefix, useUrlState } from "@/lib/url-state"
import { type WidgetId, widgets } from "@/features/embed/widgets"
import { DEFAULTS, type Thresholds as T } from "@/lib/thresholds"
import { ApiError, callTotalsQuery, invitationsQuery, latestMatchingCallQuery, mediaServerQuery, membersQuery, projectSettingsQuery, removeMember, updateMember } from "@/lib/api"
import type { Invitation, Invitations, Member, ProjectSettings } from "@/lib/api-types"
import { refreshSession, roleIn, useSession } from "@/lib/session"
import { useLocalSave } from "@/lib/local-store"
import { errorText } from "@/components/page-state"

// Settings as a modal over the page you are on (layout from shadcn's sidebar-13 block). Three groups,
// as in ShipVoice Pro's settings: the project picked at the top of the sidebar (integrations, data,
// thresholds), the organisation (its projects, Enterprise shown locked per decision 0018, and Access:
// people, API keys, MCP and the audit log as tabs) and you. What the Phase 1 console can change
// (thresholds, tags, appearance) works and is kept in this browser, with undo; what the Phase 2
// server will own is read from the captured project or says "Not set".

// The organisation and its projects as getWorkspaces named them (lib/workspaces.ts). When it named
// none, the scope's id stands in and no project is listed.
const org = () => currentOrg() ?? { id: currentScope().org, name: currentScope().org, projects: [] }
const projectName = () => org().projects.find((p) => p.id === currentScope().project)?.name ?? currentScope().project

type Key = "account" | "organisation" | "access" | "integrations" | "embeds" | "data" | "thresholds"
type Page = { key: Key; label: string; icon: Icon; scope: "project" | "organisation" | "you" }
const groups: { label: string; scope: Page["scope"]; pages: Page[] }[] = [
  {
    label: "Project",
    scope: "project",
    pages: [
      { key: "integrations", label: "Integrations", icon: Plug, scope: "project" },
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
      <Item size="sm" className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 rounded-none border-0 px-0 py-4">
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
function ServerUrl() {
  const read = useQuery(mediaServerQuery())
  if (read.isPending) return <Muted>Loading</Muted>
  if (read.isError) return <Muted>Could not be read</Muted>
  return read.data.server ? <span className="font-mono text-[13px]">{read.data.server.url}</span> : <Muted>not reported</Muted>
}

// --- Pages ---

export function Account() {
  const { theme, set } = useTheme()
  const session = useSession()
  const email = session?.email
  const role = roleIn(org().id, session)
  return (
    <>
      <Header title="Account" description="You, and how the console looks for you." />
      <Group label="Profile">
        <Row title="Name" value={<NotSet />} />
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

function Organisation() {
  const [adding, setAdding] = useState(false)
  return (
    <>
      <Header title={org().name} description="The organisation that owns your projects. The open-source edition is one organisation with any number of projects and no usage limits." />
      <Group label="Projects">
        {org().projects.map(({ name: p }, k) => (
          <Row key={p} icon={TreeStructure} title={p} description="Current project" value={<ServerUrl />} last={k === org().projects.length - 1} />
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

type Role = Invitation["role"]
type Invite = Invitation

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
  // Removing someone is not undone from here (there is no way to add them back until invitations
  // land), so it asks first.
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
                    <Button variant="ghost" size="sm" onClick={() => setRemoving(m)} aria-label={`Remove ${m.email}`}>
                      Remove
                    </Button>
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
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{`Remove ${removing?.email ?? ""}?`}</DialogTitle>
            <DialogDescription>{`They lose access to ${org().name} on their next request. Their account stays, and adding them back needs the server's user add command until invitations arrive.`}</DialogDescription>
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

// Invites wait in this browser until invitation links arrive (phase 06d); getInvitations reads
// them back. Admins only, as getInvitations is.
function Invites() {
  const read = useQuery(invitationsQuery())
  const setInvites = useLocalSave<Invite[], Invitations>("vg.invites", invitationsQuery().queryKey, (d) => d?.items ?? [])
  const list = read.data?.items ?? []
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<Role>("Member")
  const clean = email.trim().toLowerCase()
  const valid = read.isSuccess && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean) && !list.some((i) => i.email === clean)
  const invite = () => {
    const added = clean
    setInvites((current) => (current.some((x) => x.email === added) ? current : [...current, { email: added, role }]))
    setEmail("")
    toast(`Invited ${added} as ${role}`, { action: { label: "Undo", onClick: () => setInvites((current) => current.filter((x) => x.email !== added)) } })
  }
  return (
    <>
      <section className="pt-6">
        <form
          className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 border-b pb-6"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) invite()
          }}
          aria-label="Invite people"
        >
          <div className="space-y-0.5">
            <p className="text-[14px] font-medium">Invite people</p>
            <p className="text-[13px] text-muted-foreground">They get a link to join {org().name}. Links arrive with a later release; until then invites wait here.</p>
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
          </div>
        </form>
      </section>
      {list.length > 0 && (
        <Group label="Pending invitations">
          {list.map((i, k) => (
            <Row
              key={i.email}
              title={i.email}
              description={`${i.role} · invite pending`}
              value={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setInvites((current) => current.filter((x) => x.email !== i.email))
                    toast(`Revoked the invite for ${i.email}`, { action: { label: "Undo", onClick: () => setInvites((current) => (current.some((x) => x.email === i.email) ? current : [...current, i])) } })
                  }}
                >
                  Revoke
                </Button>
              }
              last={k === list.length - 1}
            />
          ))}
        </Group>
      )}
    </>
  )
}

type Integration = { logo: string; name: string; description: string; status: string; ok?: boolean; body?: React.ReactNode; icon?: Icon; connect?: boolean }

function IntegrationList({ list }: { list: Integration[] }) {
  return (
    <ItemGroup className="gap-0 has-data-[size=sm]:gap-0">
      {list.map((x, k) => (
        <span key={x.name} className="contents">
          <Item size="sm" className="flex-col items-stretch gap-3 rounded-none border-0 px-0 py-4">
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

// One copyable command, as in the reference's MCP tab. The values come from the environment, so no
// key or address is ever written into the page.
function Command({ text }: { text: string }) {
  return (
    <div className="relative rounded-lg bg-foreground px-4 py-3 pr-12 font-mono text-[12px] leading-relaxed whitespace-pre text-background">
      {text}
      <Button
        variant="ghost"
        size="icon-sm"
        className="absolute top-2 right-2 text-background/70 hover:bg-background/10 hover:text-background"
        aria-label="Copy command"
        onClick={() => {
          try {
            void navigator.clipboard?.writeText(text)
            toast("Copied")
          } catch {
            // Clipboard is unavailable; the command stays selectable.
          }
        }}
      >
        <Copy />
      </Button>
    </div>
  )
}

const mcpClients = [
  { key: "claude", name: "Claude Code", logo: "claudecode", command: 'claude mcp add --transport http voicegateway "$VOICEGATEWAY_MCP_URL" \\\n  --header "Authorization: Bearer $VOICEGATEWAY_API_KEY"' },
  { key: "codex", name: "Codex", logo: "codex", command: 'codex mcp add voicegateway --url "$VOICEGATEWAY_MCP_URL" \\\n  --bearer-token-env-var VOICEGATEWAY_API_KEY' },
  { key: "cursor", name: "Cursor", logo: "cursor", command: '{ "mcpServers": { "voicegateway": {\n  "url": "${env:VOICEGATEWAY_MCP_URL}",\n  "headers": { "Authorization": "Bearer ${env:VOICEGATEWAY_API_KEY}" } } } }' },
]

function ApiKeys() {
  return (
    <>
      <Header title="API keys" description="For agents on other frameworks, for the SDK and for MCP. Each key belongs to one project." />
      <Group footer="The server creates a key, shows it once and stores it hashed. Keys arrive with the Phase 2 server.">
        {org().projects.map(({ name: p }, k) => (
          <Row
            key={p}
            icon={KeyIcon}
            title={p}
            description="No keys yet"
            value={
              <Button variant="outline" size="sm" disabled>
                <Plus /> Create key
              </Button>
            }
            last={k === org().projects.length - 1}
          />
        ))}
      </Group>
      <Group label="Enterprise">
        <Row icon={Lock} muted title="Ingestion limits per key" description="Cap what one key or one project can send." value={<span className="text-[12px] text-muted-foreground">Open source: no limits.</span>} last />
      </Group>
    </>
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
              <Command text={c.command} />
            </TabsContent>
          ))}
        </Tabs>
      </section>
    </>
  )
}

function AuditLog() {
  return (
    <>
      <Header title="Audit log" description="Every change, who made it and when. Nobody can edit it." badge="Enterprise" />
      <div className="mt-6 flex flex-col items-center gap-2 rounded-2xl border border-dashed px-6 py-10 text-center">
        <Lock size={20} className="text-muted-foreground" />
        <p className="text-[14px] font-medium">Part of VoiceGateway Enterprise</p>
        <p className="max-w-[46ch] text-[13px] text-muted-foreground">Who changed a setting, created a key or opened a recording. The open-source edition does not record changes (decision 0017).</p>
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
        <Row title="Traces and metrics" value={days(s.traceRetentionDays)} />
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

const Thresholds = () => <WithSettings title="Thresholds">{(s) => <ThresholdsForm t={s.thresholds} />}</WithSettings>

function ThresholdsForm({ t }: { t: T }) {
  // Every page's checks and the server's judgements use these, so a save asks every read again.
  const setThresholds = useLocalSave<T>("vg.thresholds", [])
  const asDraft = (x: T) => ({ answer: String(x.answerMs / 1000), join: String(x.joinMs / 1000), tts: String(x.ttsStartMs / 1000) })
  const [draft, setDraft] = useState(asDraft(t))
  const n = (v: string) => Number(v)
  const valid = [draft.answer, draft.join, draft.tts].every((v) => v.trim() !== "" && Number.isFinite(n(v)) && n(v) > 0 && n(v) <= 60)
  const changed = n(draft.answer) * 1000 !== t.answerMs || n(draft.join) * 1000 !== t.joinMs || n(draft.tts) * 1000 !== t.ttsStartMs
  const apply = (next: T, message: string) => {
    const before = t
    setThresholds(next)
    setDraft(asDraft(next))
    toast(message, { action: { label: "Undo", onClick: () => (setThresholds(before), setDraft(asDraft(before))) } })
  }
  const field = (key: keyof typeof draft, label: string, hint: string) => (
    <Field orientation="horizontal" className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:gap-8 py-4">
      <div className="space-y-0.5">
        <FieldLabel htmlFor={`th-${key}`} className="text-[14px]">
          {label}
        </FieldLabel>
        <FieldDescription className="text-[13px]">{hint}</FieldDescription>
      </div>
      <InputGroup className="w-28">
        <InputGroupInput id={`th-${key}`} type="number" min={0.1} max={60} step={0.1} value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} className="tnum" />
        <InputGroupAddon align="inline-end">s</InputGroupAddon>
      </InputGroup>
    </Field>
  )
  return (
    <>
      <Header title="Thresholds" description="The numbers this project's checks judge calls against. Settings, not measurements; every page uses the same ones." badge="This browser" />
      <FieldGroup className="gap-0 pt-2 [&>[data-slot=field]+[data-slot=field]]:border-t">
        {field("answer", "Answer time", "Caller finishing to the agent's first audio.")}
        {field("join", "Agent join", "Dispatch to the agent joining the room.")}
        {field("tts", "Speech start", "Text to speech's time to first audio.")}
      </FieldGroup>
      <div className="flex items-center justify-end gap-3 border-t pt-5">
        {!valid && <span className="mr-auto text-[13px] text-destructive">Use a number between 0.1 and 60.</span>}
        <Button variant="outline" onClick={() => apply(DEFAULTS, "Thresholds reset to defaults")}>
          Reset to defaults
        </Button>
        <Button disabled={!valid || !changed} onClick={() => apply({ answerMs: n(draft.answer) * 1000, joinMs: n(draft.join) * 1000, ttsStartMs: n(draft.tts) * 1000 }, "Thresholds saved")}>
          Save changes
        </Button>
      </div>
    </>
  )
}

// Widgets a platform puts in its own product (decision 0023). The preview is the real widget in a frame.
function Embeds() {
  const origins = useQuery(projectSettingsQuery())
  const [pick, setPick] = useUrlState<WidgetId>("widget", "answer-time")
  const [theme, setTheme] = useState<"light" | "dark">("light")
  const w = widgets.find((x) => x.id === pick) ?? widgets[0]
  // The call widget previews the newest call with a turn; other widgets need no call.
  const latest = useQuery({ ...latestMatchingCallQuery(new URLSearchParams({ turnsMin: "1" })), enabled: pick === "call" })
  const callId = latest.data?.id
  const src = `${window.location.origin}${scopePrefix()}/embed/${w.id}`
  const preview = `${src}?theme=${theme}${w.id === "call" && callId ? `&call=${encodeURIComponent(callId)}` : ""}`
  const snippet = `<iframe\n  src="${src}?token=EMBED_TOKEN&theme=${theme}${w.id === "call" ? "&call=CALL_ID" : ""}"\n  width="100%"\n  height="${w.height}"\n  style="border: 0"\n  title="${w.title}, VoiceGateway"\n></iframe>`
  return (
    <>
      <Header title="Embeds" description="Put this project's views in your own product. Each widget is a frame behind a short-lived, read-only token, so your API key never reaches the page." />
      <section className="space-y-3 pt-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Select value={w.id} onValueChange={(v) => setPick(v as WidgetId)}>
            <SelectTrigger size="sm" className="w-56" aria-label="Widget">
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
          <ToggleGroup type="single" variant="outline" size="sm" value={theme} onValueChange={(v) => v && setTheme(v as "light" | "dark")} aria-label="Widget theme">
            <ToggleGroupItem value="light" className="px-3 data-[state=on]:bg-accent">
              Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark" className="px-3 data-[state=on]:bg-accent">
              Dark
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
        <p className="text-[13px] text-muted-foreground">{w.description}</p>
        {w.id === "call" && !callId ? (
          <p className="rounded-xl border px-4 py-10 text-center text-[13px] text-muted-foreground">
            {latest.isPending ? "Loading a call for the preview." : latest.isError ? `A call for the preview could not be read. ${errorText(latest.error)}` : "No call with a turn yet, so there is nothing to preview."}
          </p>
        ) : (
          <iframe key={preview} src={preview} title={`${w.title} preview`} className="block w-full rounded-xl border" style={{ height: Math.min(w.height, 420) }} />
        )}
      </section>
      <section className="pt-6">
        <h3 className="pb-1 text-[14px] font-semibold">Snippet</h3>
        <p className="pb-3 text-[13px] text-muted-foreground">Your backend asks VoiceGateway for a token and puts it in place of EMBED_TOKEN.</p>
        <Command text={snippet} />
      </section>
      <Group label="Access" footer="Tokens and allowed origins are kept on the server, from Phase 2. Until then widgets show captured data and say they are a preview.">
        <Row
          title="Embed tokens"
          description="Read-only, one project, and they expire."
          value={
            <Button variant="outline" size="sm" disabled>
              <Plus /> Create token
            </Button>
          }
        />
        <Row title="Allowed origins" description="Only these sites can show the widgets." value={origins.data ? (origins.data.allowedOrigins?.join(", ") ?? <NotSet />) : origins.isError ? "Could not be read" : "Loading"} last />
      </Group>
      <Group label="Enterprise">
        <Row icon={Lock} muted title="Per-customer tokens" description="A token that shows only one of your customers' calls." />
        <Row icon={Lock} muted title="Remove VoiceGateway branding" description="Widgets without Powered by VoiceGateway." value={<span className="text-[12px] text-muted-foreground">Open source: always shown.</span>} last />
      </Group>
    </>
  )
}

const body: Record<Key, () => React.ReactNode> = { account: Account, organisation: Organisation, access: Access, integrations: Integrations, embeds: Embeds, data: Data, thresholds: Thresholds }

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
  const at = `${scope.org}/${scope.project}`
  const [pick, setPick] = useState({ at, name: projectName() })
  const picked = pick.at === at ? pick.name : projectName()
  const setPicked = (name: string) => setPick({ at, name })
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
              {current.scope === "you" ? "Your own account." : current.scope === "organisation" ? `Settings for ${org().name}.` : `Settings for ${picked}.`}{" "}
              <span className="whitespace-nowrap">
                Open anywhere with{" "}
                <KbdGroup>
                  <Kbd>⌘</Kbd>
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
                        {org().projects.map(({ name: p }) => (
                          <SelectItem key={p} value={p}>
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
                    {org().projects.map(({ name: p }) => (
                      <SelectItem key={p} value={p}>
                        {p}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <div className="flex-1 overflow-y-auto px-4 pt-6 pb-10 sm:px-8">
              <Body />
            </div>
          </main>
        </SidebarProvider>
      </DialogContent>
    </Dialog>
  )
}
