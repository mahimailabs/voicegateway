import { useState } from "react"
import { Link, useRouter, useRouterState } from "@tanstack/react-router"
import { currentOrg, currentScope } from "@/lib/url-state"
import { CaretUpDown, Check, Gear, Info, Lock, Plus, TreeStructure } from "@phosphor-icons/react"
import { NewProjectDialog } from "@/components/new-project-dialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { workspaces } from "@/lib/workspaces"
import { data as media } from "@/features/media-server/room-drawer"

// Organisation and project as two switchers in the top bar, the way the LiveKit and Vercel consoles
// do it: org / project / page. A second organisation is Enterprise (decision 0017), so it shows
// locked instead of hidden; projects open their settings from here.

// A menu item that opens a dialog: the menu skips handing focus back to its trigger, so the dialog
// never hides a focused element. Other items keep the normal focus return.
let opening = false
const later = (fn: () => void) => {
  opening = true
  setTimeout(fn, 120)
}
const keepFocus = (e: Event) => {
  if (opening) e.preventDefault()
  opening = false
}
const trigger = "flex h-8 min-w-0 items-center gap-2 rounded-lg whitespace-nowrap px-2 text-[13px] font-medium transition-colors hover:bg-muted data-[state=open]:bg-muted"
const initials = (s: string) => s.trim()[0]?.toUpperCase() ?? "?"

// Without an organisation from getWorkspaces there is nothing to switch, so neither switcher shows.
export function OrgSwitcher({ onSettings }: { onSettings: () => void }) {
  const org = currentOrg()
  if (!org) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={trigger}>
        <span className="flex size-5 items-center justify-center rounded-md bg-foreground text-[10px] font-semibold text-background">{initials(org.name)}</span>
        <span className="hidden sm:inline">{org.name}</span>
        <CaretUpDown size={13} className="text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-64 rounded-xl" onCloseAutoFocus={keepFocus}>
        <DropdownMenuLabel className="text-xs text-muted-foreground">Organisations</DropdownMenuLabel>
        {workspaces().map((o) => (
          <DropdownMenuItem key={o.name} className="gap-2">
            <span className="flex size-5 items-center justify-center rounded-md bg-foreground text-[10px] font-semibold text-background">{initials(o.name)}</span>
            {o.name}
            {o.id === org.id && <Check className="ml-auto size-4" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem className="gap-2" onSelect={() => later(onSettings)}>
          <Gear className="size-4" /> Organisation settings
        </DropdownMenuItem>
        <Tooltip>
          <TooltipTrigger asChild>
            <div>
              <DropdownMenuItem disabled className="gap-2">
                <Lock className="size-4" /> New organisation
                <span className="ml-auto rounded-full border px-1.5 text-[10px]">Enterprise</span>
              </DropdownMenuItem>
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" className="max-w-60 text-xs">
            More than one organisation on one install is VoiceGateway Enterprise (decision 0017).
          </TooltipContent>
        </Tooltip>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function ProjectSwitcher({ onManage }: { onManage: () => void }) {
  const org = currentOrg()
  const router = useRouter()
  const path = useRouterState({ select: (s) => s.location.pathname })
  const [adding, setAdding] = useState(false)
  const current = org?.projects.find((p) => p.id === currentScope().project) ?? org?.projects[0]
  if (!org || !current) return null
  // Switching project keeps the page and moves the URL to the other project's scope.
  const open = (id: string) => id !== current.id && router.history.push(`/org/${org.id}/project/${id}${path}`)
  const host = media.server?.url.replace(/^wss?:\/\//, "") ?? "server not reported"
  return (
    <>
      <NewProjectDialog open={adding} onOpenChange={setAdding} />
      <DropdownMenu>
        <DropdownMenuTrigger className={trigger}>
          <TreeStructure size={15} className="text-muted-foreground" />
          <span className="truncate">{current.name}</span>
          <CaretUpDown size={13} className="text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-72 rounded-xl" onCloseAutoFocus={keepFocus}>
          <DropdownMenuLabel className="text-xs text-muted-foreground">{`Projects in ${org.name}`}</DropdownMenuLabel>
          {org.projects.map((p) => (
            <DropdownMenuItem key={p.id} className="gap-2" onSelect={() => open(p.id)}>
              <TreeStructure className="size-4 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block">{p.name}</span>
                <span className="block truncate font-mono text-[11px] text-muted-foreground">{host}</span>
              </span>
              {p.id === current.id && <Check className="ml-auto size-4" />}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem className="gap-2" asChild>
            <Link to="/project">
              <Info className="size-4" /> What this project can reach
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem className="gap-2" onSelect={() => setAdding(true)}>
            <Plus className="size-4" /> Add a LiveKit project
          </DropdownMenuItem>
          <DropdownMenuItem className="gap-2" onSelect={() => later(onManage)}>
            <Gear className="size-4" /> Manage projects
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
