import { CopyCommand } from "@/components/copy-command"
import { workspacesRead } from "@/lib/workspaces"
import { Button } from "@/components/ui/button"
import { useMemo, useState } from "react"
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router"
import {
  Bell,
  MagnifyingGlass,
  TreeStructure,
  WarningCircle,
  Pulse as Activity,
  DownloadSimple as ArrowDownToLine,
  UploadSimple as ArrowUpFromLine,
  Robot as Bot,
  BookOpenText as BookOpen,
  SquaresFour as LayoutGrid,
  Phone,
  PhoneCall,
  HardDrives as Server,
  GearSix as Settings,
} from "@phosphor-icons/react"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { ThemeToggle } from "@/components/theme"
import { UserMenu } from "@/components/user-menu"
import { useQuery } from "@tanstack/react-query"
import { callCountQuery, projectSettingsQuery } from "@/lib/api"
import { DEFAULTS, applyThresholds } from "@/lib/thresholds"
import { PageState, errorText } from "@/components/page-state"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { SettingsDialog, type SettingsPage } from "@/features/settings/settings-dialog"
import { currentOrg, firstProjectPath, orgOnly, scopeUnknown, useUrlState } from "@/lib/url-state"
import { OrgSwitcher, ProjectSwitcher } from "@/components/org-switcher"
import { BrandWordmark } from "@/components/brand-wordmark"
import { motion } from "motion/react"
import { CommandPalette, ShortcutsDialog, mod, useCommandHotkeys } from "@/components/command-palette"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Toaster } from "@/components/ui/sonner"

type Item = { to: string; label: string; icon: typeof Activity; badge?: string; key?: string }
// Grouped by what you are doing: watch the calls, then the agents, then the LiveKit services under them.
// The project's own page opens from the project switcher.
const groups: { label: string | null; items: Item[] }[] = [
  { label: null, items: [{ to: "/overview", label: "Overview", icon: LayoutGrid, key: "o" }] },
  {
    label: "Observe",
    items: [
      { to: "/live", label: "Live", icon: Activity, key: "l" },
      { to: "/calls", label: "Calls", icon: PhoneCall, key: "c" },
      { to: "/issues", label: "Issues", icon: WarningCircle, key: "i" },
      { to: "/alerts", label: "Alerts", icon: Bell, key: "a" },
    ],
  },
  { label: "Agents", items: [{ to: "/agents", label: "Agent workers", icon: Bot, key: "w" }] },
  {
    label: "LiveKit",
    items: [
      { to: "/sfu", label: "Media server", icon: Server, key: "m" },
      { to: "/sip", label: "Phone (SIP)", icon: Phone, key: "p" },
      { to: "/egress", label: "Egress", icon: ArrowUpFromLine, key: "e" },
      { to: "/ingress", label: "Ingress", icon: ArrowDownToLine, key: "n" },
    ],
  },
]

export function AppShell() {
  const path = useRouterState({ select: (s) => s.location.pathname })
  const navigate = useNavigate()
  // Settings is a modal over the current page, kept in the URL (?settings=access) so a link opens it:
  // the sidebar item, Cmd or Ctrl + comma, or /settings.
  const [settingsParam, setSettingsParam] = useUrlState<string | null>("settings", null)
  // A link to an organisation or project this person cannot read asks nothing of the API, and Settings
  // stays closed: there is no project to show settings for.
  const unknown = scopeUnknown()
  // An organisation with no project yet still opens its organisation pages.
  const orgSettings = orgOnly()
  const openSettings = (page?: SettingsPage) => (!unknown || orgSettings) && setSettingsParam(page ?? (unknown ? "access" : "integrations"))
  const settingsOpen = (!unknown || orgSettings) && (settingsParam !== null || path === "/settings")
  const closeSettings = (o: boolean) => {
    if (o) return
    if (path === "/settings") navigate({ to: "/overview" })
    else navigate({ to: ".", replace: true, search: (prev: Record<string, unknown>) => ({ ...prev, settings: undefined, access: undefined, widget: undefined }) } as never)
  }
  const [palette, setPalette] = useState(false)
  const [shortcuts, setShortcuts] = useState(false)
  const pages = useMemo(() => [...groups.flatMap((g) => g.items), { to: "/project", label: "Project", icon: TreeStructure, key: "j" }], [])
  useCommandHotkeys({ pages, onPalette: () => setPalette((o) => !o), onSettings: () => openSettings(), onShortcuts: () => setShortcuts(true) })
  // Pages judge calls against the project's thresholds, so they wait for the settings read, and a
  // threshold change renders the page again. Settings that could not be read leave the contract's
  // defaults in place, and the page says so.
  const settings = useQuery({ ...projectSettingsQuery(), enabled: !unknown })
  const thresholds = settings.data?.thresholds ?? DEFAULTS
  applyThresholds(thresholds)
  // The Calls badge is the server's count of every call; nothing shows until it answers.
  const callCount = useQuery({ ...callCountQuery(new URLSearchParams()), enabled: !unknown }).data
  const badgeOf = (it: Item) => (it.to === "/calls" ? (callCount == null ? undefined : String(callCount)) : it.badge)
  // The current page carries a pill that slides between items as you move.
  const nav = (it: Item) => {
    const on = path.startsWith(it.to)
    return (
      <SidebarMenuItem key={it.to} className="relative">
        {on && <motion.span layoutId="nav-active" transition={{ type: "spring", stiffness: 500, damping: 38 }} className="absolute inset-0 rounded-lg bg-background shadow-xs ring-1 ring-border" />}
        <SidebarMenuButton
          asChild
          isActive={on}
          tooltip={it.label}
          className="relative h-8 rounded-lg text-[13.5px] font-medium text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground data-[active=true]:bg-transparent data-[active=true]:text-foreground"
        >
          <Link to={it.to}>
            <it.icon className={on ? "text-foreground" : ""} />
            <span>{it.label}</span>
          </Link>
        </SidebarMenuButton>
        {badgeOf(it) && (
          <SidebarMenuBadge style={{ top: "50%", transform: "translateY(-50%)" }} className="h-5 rounded-md bg-foreground/[0.07] px-1.5 text-[10.5px] !text-muted-foreground tnum">
            {badgeOf(it)}
          </SidebarMenuBadge>
        )}
      </SidebarMenuItem>
    )
  }

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader className="px-3 pt-3.5 pb-2">
          <div className="flex h-8 min-w-0 items-center gap-2.5">
            <BrandWordmark />
            <SidebarTrigger className="ml-auto text-muted-foreground group-data-[collapsible=icon]:mx-auto" />
          </div>
        </SidebarHeader>
        <SidebarContent>
          {/* On "No such project" the page links lead nowhere useful, so only its own link to a project shows. */}
          {!unknown && groups.map((g, i) => (
            <SidebarGroup key={i}>
              {g.label && <SidebarGroupLabel className="text-[10.5px] font-semibold tracking-[0.08em] text-muted-foreground/80 uppercase">{g.label}</SidebarGroupLabel>}
              <SidebarMenu className="gap-0.5">{g.items.map(nav)}</SidebarMenu>
            </SidebarGroup>
          ))}
        </SidebarContent>
        <SidebarFooter className="pb-3">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild tooltip="Documentation">
                <a href="https://docs.voicegateway.dev" target="_blank" rel="noreferrer">
                  <BookOpen />
                  <span>Documentation</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton isActive={settingsOpen} disabled={unknown && !orgSettings} tooltip={`Settings (${mod} ,)`} onClick={() => openSettings()}>
                <Settings />
                <span>Settings</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <div className="mt-1 border-t pt-2">
            <UserMenu />
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur md:px-6">
          <SidebarTrigger className="-ml-1 text-muted-foreground lg:hidden" />
          <OrgSwitcher onSettings={() => openSettings("organisation")} />
          {currentOrg() && <span className="text-muted-foreground/50">/</span>}
          <ProjectSwitcher onManage={() => openSettings("organisation")} />
          <div className="ml-auto flex items-center gap-1.5">
            <button type="button" onClick={() => setPalette(true)} className="mr-1 hidden h-8 w-60 items-center gap-2 rounded-lg border bg-muted/40 pr-1.5 pl-2.5 text-[13px] text-muted-foreground transition-colors hover:bg-muted lg:flex">
              <MagnifyingGlass size={15} />
              Search or jump to
              <KbdGroup className="ml-auto">
                <Kbd>{mod}</Kbd>
                <Kbd>K</Kbd>
              </KbdGroup>
            </button>
            <Button variant="ghost" size="icon-sm" className="text-muted-foreground lg:hidden" onClick={() => setPalette(true)} aria-label="Search or jump to">
              <MagnifyingGlass size={16} />
            </Button>
            <ThemeToggle />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1200px] space-y-6 px-4 pt-8 pb-32 md:px-8">
          {unknown ? (
            <NoSuchProject />
          ) : (
            <>
              {settings.isError && <p className="rounded-lg border bg-muted px-3 py-2 text-[13px] text-muted-foreground">{`Project settings could not be read, so checks use the default thresholds. ${errorText(settings.error)}`}</p>}
              {settings.isPending ? <PageState icon={Settings} loading title="Loading the project's settings" /> : <Outlet key={`${thresholds.answerMs}-${thresholds.joinMs}-${thresholds.ttsStartMs}`} />}
            </>
          )}
        </main>
      </SidebarInset>
      <SettingsDialog open={settingsOpen} onOpenChange={closeSettings} page={settingsParam} onPageChange={openSettings} orgOnly={unknown} />
      <CommandPalette open={palette} onOpenChange={setPalette} pages={pages} onSettings={() => openSettings()} onShortcuts={() => setShortcuts(true)} />
      <ShortcutsDialog open={shortcuts} onOpenChange={setShortcuts} pages={pages} />
      <Toaster position="bottom-right" duration={8000} />
    </SidebarProvider>
  )
}

// The command that adds a LiveKit project to a running install (README, make project-add).
const PROJECT_ADD_COMMAND =
  'docker compose exec -e LIVEKIT_API_SECRET web voicegateway project add --name "Production" --livekit-url "$LIVEKIT_URL" --livekit-api-key "$LIVEKIT_API_KEY"'

// The URL names an organisation or project this person cannot read. Say so, and link to the first
// project they can, as a full page load so that project's settings are read. When they can read no
// project at all, as in a fresh install, say how to add one instead.
function NoSuchProject() {
  const first = firstProjectPath()
  if (!first && workspacesRead()) {
    return (
      <Empty className="mx-auto max-w-[1200px]">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <TreeStructure />
          </EmptyMedia>
          <EmptyTitle>No project yet</EmptyTitle>
          <EmptyDescription>A project is one LiveKit project. Add yours on the server, with its API secret in LIVEKIT_API_SECRET, then reload.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="w-full max-w-xl">
          <CopyCommand text={PROJECT_ADD_COMMAND} label="Project add command" />
        </EmptyContent>
      </Empty>
    )
  }
  return (
    <Empty className="mx-auto max-w-[1200px]">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <TreeStructure />
        </EmptyMedia>
        <EmptyTitle>No such project</EmptyTitle>
        <EmptyDescription>This link names an organisation or project that does not exist, or one you cannot read.</EmptyDescription>
      </EmptyHeader>
      {first && (
        <EmptyContent>
          <Button size="sm" variant="outline" asChild>
            <a href={first}>Open your first project</a>
          </Button>
        </EmptyContent>
      )}
    </Empty>
  )
}
