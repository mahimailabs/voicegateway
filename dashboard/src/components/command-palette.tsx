import { useEffect, useMemo, useRef } from "react"
import { useNavigate } from "@tanstack/react-router"
import { ArrowSquareOut, BookOpenText, GearSix, Keyboard, MoonStars, PhoneCall, Robot, Sun, WarningCircle } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut } from "@/components/ui/command"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { useTheme } from "@/components/theme"
import { useQuery } from "@tanstack/react-query"
import { cloudAgentsQuery, issuesQuery, mediaServerQuery, recentCallsQuery } from "@/lib/api"
import { fromContract } from "@/features/media-server/room-drawer"
import { agentsFrom } from "@/features/agents/agents-model"
import { issuesFrom } from "@/features/issues/issues-model"

// One palette for the whole console (cmdk through shadcn's Command): pages, calls, agents, issues and
// actions. Hotkeys follow Linear's pattern: G then a letter goes to a page, so single keys stay free
// for each page's own shortcuts ("/" to search).

export type NavPage = { to: string; label: string; icon: Icon; key?: string }
const DOCS = "https://docs.voicegateway.dev"
export const mod = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl"

const typing = (t: EventTarget | null) => {
  const el = t as HTMLElement | null
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))
}

export function useCommandHotkeys({ pages, onPalette, onSettings, onShortcuts }: { pages: NavPage[]; onPalette: () => void; onSettings: () => void; onShortcuts: () => void }) {
  const navigate = useNavigate()
  const { toggle } = useTheme()
  const pendingG = useRef(0)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase()
      if ((e.metaKey || e.ctrlKey) && k === "k") return (e.preventDefault(), onPalette())
      if ((e.metaKey || e.ctrlKey) && e.key === ",") return (e.preventDefault(), onSettings())
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && k === "l") return (e.preventDefault(), toggle())
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector('[role="dialog"]')) return
      if (e.key === "?") return (e.preventDefault(), onShortcuts())
      if (k === "g") {
        pendingG.current = Date.now()
        return
      }
      if (Date.now() - pendingG.current < 1200) {
        const page = pages.find((p) => p.key === k)
        pendingG.current = 0
        if (page) {
          e.preventDefault()
          navigate({ to: page.to })
        }
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [pages, navigate, onPalette, onSettings, onShortcuts, toggle])
}

const Keys = ({ keys }: { keys: string[] }) => (
  <KbdGroup>
    {keys.map((k) => (
      <Kbd key={k}>{k}</Kbd>
    ))}
  </KbdGroup>
)

export function CommandPalette({ open, onOpenChange, pages, onSettings, onShortcuts }: { open: boolean; onOpenChange: (o: boolean) => void; pages: NavPage[]; onSettings: () => void; onShortcuts: () => void }) {
  const navigate = useNavigate()
  const { theme, toggle } = useTheme()
  // Read when the palette opens; each list shows what has answered so far.
  const recent = useQuery({ ...recentCallsQuery(), enabled: open })
  const calls = recent.data ?? []
  const cloud = useQuery({ ...cloudAgentsQuery(), enabled: open }).data
  const media = useQuery({ ...mediaServerQuery(), enabled: open }).data
  const fleet = useMemo(() => (cloud && media ? agentsFrom(cloud, fromContract(media)) : []), [cloud, media])
  const issueRead = useQuery({ ...issuesQuery(), enabled: open })
  const issueList = issueRead.data
  // "Nothing matches" only once the reads have answered; until then the palette says it is loading.
  const loading = open && (recent.isPending || issueRead.isPending || !cloud || !media)
  const found = useMemo(() => (issueList ? issuesFrom(issueList) : []), [issueList])
  const run = (fn: () => void) => {
    onOpenChange(false)
    fn()
  }
  const when = (iso: string) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Command palette" description="Jump to a page, a call, an agent or an issue, or run an action." className="sm:max-w-[600px]">
      <Command className="[&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium">
        <CommandInput placeholder="Search pages, calls, agents, issues..." />
        <CommandList className="max-h-[420px]">
          <CommandEmpty>{loading ? "Loading…" : "Nothing matches."}</CommandEmpty>
          <CommandGroup heading="Go to">
            {pages.map((p) => (
              <CommandItem key={p.to} value={`page ${p.label}`} onSelect={() => run(() => navigate({ to: p.to }))}>
                <p.icon />
                {p.label}
                {p.key && (
                  <CommandShortcut>
                    <Keys keys={["G", p.key.toUpperCase()]} />
                  </CommandShortcut>
                )}
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading={calls.length >= 100 ? "Calls, 100 most recent" : "Calls"}>
            {calls.map((c) => (
              <CommandItem key={c.id} value={`call ${c.room} ${c.startedAt}`} onSelect={() => run(() => navigate({ to: "/calls/$callId", params: { callId: c.id } }))}>
                <PhoneCall />
                <span className="truncate font-mono text-[12px]">{c.room}</span>
                <span className="ml-auto shrink-0 text-[12px] text-muted-foreground tnum">{when(c.startedAt)}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Agents">
            {fleet.map((a) => (
              <CommandItem key={a.key} value={`agent ${a.name ?? a.key}`} onSelect={() => run(() => navigate({ to: "/agents", search: { agent: a.key } as never }))}>
                <Robot />
                {a.name ?? "Unnamed agent"}
                <span className="ml-auto text-[12px] text-muted-foreground">{a.cloud ? "LiveKit Cloud" : "self-hosted"}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Issues">
            {found.map((i) => (
              <CommandItem key={i.id} value={`issue ${i.title} ${i.source}`} onSelect={() => run(() => navigate({ to: "/issues" }))}>
                <WarningCircle className={i.severity === "high" ? "text-destructive" : i.severity === "medium" ? "text-amber-600" : ""} />
                <span className="truncate">{i.title}</span>
                <span className="ml-auto shrink-0 text-[12px] text-muted-foreground tnum">{`${i.kind === "standing" ? "Current" : i.hits.length} · ${i.source}`}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Actions">
            <CommandItem value="action settings preferences" onSelect={() => run(onSettings)}>
              <GearSix />
              Open settings
              <CommandShortcut>
                <Keys keys={[mod, ","]} />
              </CommandShortcut>
            </CommandItem>
            <CommandItem value="action theme dark light mode" onSelect={() => run(toggle)}>
              {theme === "light" ? <MoonStars /> : <Sun />}
              {theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
              <CommandShortcut>
                <Keys keys={[mod, "⇧", "L"]} />
              </CommandShortcut>
            </CommandItem>
            <CommandItem value="action keyboard shortcuts hotkeys" onSelect={() => run(onShortcuts)}>
              <Keyboard />
              Keyboard shortcuts
              <CommandShortcut>
                <Keys keys={["?"]} />
              </CommandShortcut>
            </CommandItem>
            <CommandItem value="action documentation docs help" onSelect={() => run(() => window.open(DOCS, "_blank", "noreferrer"))}>
              <BookOpenText />
              Documentation
              <ArrowSquareOut className="ml-auto opacity-60" />
            </CommandItem>
          </CommandGroup>
        </CommandList>
        <div className="flex items-center gap-4 border-t px-3 py-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Keys keys={["↑", "↓"]} /> move
          </span>
          <span className="flex items-center gap-1.5">
            <Keys keys={["↵"]} /> open
          </span>
          <span className="flex items-center gap-1.5">
            <Keys keys={["esc"]} /> close
          </span>
        </div>
      </Command>
    </CommandDialog>
  )
}

export function ShortcutsDialog({ open, onOpenChange, pages }: { open: boolean; onOpenChange: (o: boolean) => void; pages: NavPage[] }) {
  const general: [string, string[]][] = [
    ["Command palette", [mod, "K"]],
    ["Settings", [mod, ","]],
    ["Dark or light mode", [mod, "⇧", "L"]],
    ["Search on the page", ["/"]],
    ["This list", ["?"]],
  ]
  const Row = ({ label, keys }: { label: string; keys: string[] }) => (
    <li className="flex items-center justify-between py-1.5 text-[13px]">
      {label}
      <Keys keys={keys} />
    </li>
  )
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Keyboard size={20} /> Keyboard shortcuts
          </DialogTitle>
          <DialogDescription>Press G, then a letter, to go to a page.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-8 sm:grid-cols-2">
          <section>
            <h3 className="pb-1 text-[12px] font-medium text-muted-foreground">General</h3>
            <ul className="divide-y">
              {general.map(([l, k]) => (
                <Row key={l} label={l} keys={k} />
              ))}
            </ul>
          </section>
          <section>
            <h3 className="pb-1 text-[12px] font-medium text-muted-foreground">Go to</h3>
            <ul className="divide-y">
              {pages
                .filter((p) => p.key)
                .map((p) => (
                  <Row key={p.to} label={p.label} keys={["G", p.key!.toUpperCase()]} />
                ))}
            </ul>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  )
}
