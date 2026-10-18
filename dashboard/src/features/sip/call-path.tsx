import { Buildings, Cloud, DeviceMobile, LockSimple, LockSimpleOpen, Question, Robot, Users, Warning } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

// One phone call drawn hop by hop, from the phone to the agent (or back out). Each link says how it is
// carried and whether it is encrypted; each hop carries the checks that apply to it. Links LiveKit
// does not report (the phone network, the inbound transport) say so instead of guessing.

export type Hop = { icon: Icon; title: string; sub?: string; tags?: string[]; issues?: { title: string; severity: "high" | "medium" | "low" }[]; onClick?: () => void; hint?: string; extra?: React.ReactNode }
export type Link = { label: string; secure: boolean | null; note: string }
export type Path = { hops: Hop[]; links: Link[] }

const issueTone = { high: "bg-destructive text-white", medium: "bg-amber-500 text-white", low: "bg-muted-foreground/60 text-white" }

function HopView({ hop, compact }: { hop: Hop; compact?: boolean }) {
  const I = hop.icon
  const worst = hop.issues?.find((i) => i.severity === "high") ?? hop.issues?.find((i) => i.severity === "medium") ?? hop.issues?.[0]
  return (
    <div className={cn("flex shrink-0 flex-col items-center text-center", compact ? "w-32" : "w-36")}>
      <div className="relative">
        {hop.onClick ? (
          <button type="button" onClick={hop.onClick} aria-label={hop.hint ?? hop.title} title={hop.hint} className={cn("flex items-center justify-center bg-background shadow-sm ring-1 ring-border transition hover:ring-2 hover:ring-primary", compact ? "size-10 rounded-xl" : "size-14 rounded-2xl")}>
            <I size={compact ? 18 : 24} />
          </button>
        ) : (
          <span className={cn("flex items-center justify-center bg-background shadow-sm ring-1 ring-border", compact ? "size-10 rounded-xl" : "size-14 rounded-2xl")}>
            <I size={compact ? 18 : 24} />
          </span>
        )}
        {worst && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn("absolute -top-1.5 -right-1.5 flex size-5 cursor-help items-center justify-center rounded-full text-[11px] font-semibold", issueTone[worst.severity])}>{hop.issues!.length}</span>
            </TooltipTrigger>
            <TooltipContent className="max-w-64 text-xs">
              <ul className="space-y-0.5">
                {hop.issues!.map((i) => (
                  <li key={i.title}>{i.title}</li>
                ))}
              </ul>
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      <p className={cn("font-medium", compact ? "mt-1.5 text-[13px]" : "mt-3 text-[14px]")}>{hop.title}</p>
      {hop.sub && <p className="mt-0.5 max-w-full truncate font-mono text-[11px] text-muted-foreground">{hop.sub}</p>}
      {hop.extra && <div className="mt-1.5 flex flex-wrap justify-center gap-1.5">{hop.extra}</div>}
      {hop.tags && (
        <div className="mt-2 flex flex-wrap justify-center gap-1">
          {hop.tags.map((t) => (
            <span key={t} className="rounded-md bg-background px-1.5 py-0.5 text-[11px] text-muted-foreground ring-1 ring-border">
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function LinkView({ link, compact }: { link: Link; compact?: boolean }) {
  const L = link.secure === null ? Question : link.secure ? LockSimple : LockSimpleOpen
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className={cn("flex min-w-16 flex-1 cursor-help flex-col items-center gap-1.5", compact ? "mt-3" : "mt-7")}>
          <span className={cn("flex items-center gap-1 text-[11px] whitespace-nowrap", link.secure === false ? "text-destructive" : link.secure ? "text-primary" : "text-muted-foreground")}>
            <L size={12} weight="bold" />
            {link.label}
          </span>
          <span className={cn("h-1 w-full rounded-full", link.secure === false ? "bg-destructive/70" : link.secure ? "bg-primary/70" : "bg-[repeating-linear-gradient(90deg,var(--muted-foreground)_0_4px,transparent_4px_8px)] opacity-40")} />
        </div>
      </TooltipTrigger>
      <TooltipContent className="max-w-64 text-xs">{link.note}</TooltipContent>
    </Tooltip>
  )
}

// The key for the link colours and issue badges, shown once above all paths.
export function PathLegend() {
  return (
    <span className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1">
        <span className="h-1 w-4 rounded-full bg-primary/70" /> encrypted
      </span>
      <span className="flex items-center gap-1">
        <span className="h-1 w-4 rounded-full bg-destructive/70" /> not encrypted
      </span>
      <span className="flex items-center gap-1">
        <span className="h-1 w-4 rounded-full bg-[repeating-linear-gradient(90deg,var(--muted-foreground)_0_3px,transparent_3px_6px)] opacity-40" /> not reported
      </span>
      <span className="flex items-center gap-1">
        <Warning size={12} /> issues on that hop
      </span>
    </span>
  )
}

export function PathDiagram({ path, compact, vertical }: { path: Path; compact?: boolean; vertical?: boolean }) {
  if (vertical) return <PathColumn path={path} />
  return (
    <div role="region" aria-label="SIP call path" tabIndex={0} className={cn("flex items-start overflow-x-auto px-2 pb-1 focus-visible:outline-2 focus-visible:outline-ring", compact ? "pt-2" : "pt-3")}>
      {path.hops.map((h, k) => (
        <div key={`${h.title}${k}`} className="contents">
          <HopView hop={h} compact={compact} />
          {path.links[k] && <LinkView link={path.links[k]} compact={compact} />}
        </div>
      ))}
    </div>
  )
}

// The same path top to bottom, for narrow panes: each hop on a row, its link running down to the next.
function PathColumn({ path }: { path: Path }) {
  return (
    <ol className="space-y-0">
      {path.hops.map((h, k) => {
        const I = h.icon
        const link = path.links[k]
        const L = link ? (link.secure === null ? Question : link.secure ? LockSimple : LockSimpleOpen) : null
        const worst = h.issues?.find((i) => i.severity === "high") ?? h.issues?.find((i) => i.severity === "medium") ?? h.issues?.[0]
        const tile = (
          <span className="relative flex size-10 shrink-0 items-center justify-center rounded-xl bg-background shadow-sm ring-1 ring-border">
            <I size={18} />
            {worst && <span className={cn("absolute -top-1.5 -right-1.5 flex size-[18px] items-center justify-center rounded-full text-[10px] font-semibold", issueTone[worst.severity])}>{h.issues!.length}</span>}
          </span>
        )
        return (
          <li key={`${h.title}${k}`}>
            <div className="flex items-start gap-3">
              {h.onClick ? (
                <button type="button" onClick={h.onClick} aria-label={h.hint ?? h.title} title={h.hint} className="rounded-xl transition hover:ring-2 hover:ring-primary">
                  {tile}
                </button>
              ) : (
                tile
              )}
              <div className="min-w-0 flex-1 pt-0.5">
                <p className="flex items-baseline gap-2 text-[13px] font-medium">
                  {h.title}
                  {h.sub && <span className="truncate font-mono text-[11px] font-normal text-muted-foreground">{h.sub}</span>}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {h.extra}
                  {h.tags?.map((t) => (
                    <span key={t} className="rounded-md bg-background px-1.5 py-0.5 text-[11px] text-muted-foreground ring-1 ring-border">
                      {t}
                    </span>
                  ))}
                  {h.issues?.map((i) => (
                    <span key={i.title} className={cn("text-[11px]", i.severity === "high" ? "text-destructive" : i.severity === "medium" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>
                      {i.title}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            {link && L && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex cursor-help items-center gap-3 py-1.5">
                    <span className="flex w-10 justify-center">
                      <span className={cn("h-8 w-1 rounded-full", link.secure === false ? "bg-destructive/70" : link.secure ? "bg-primary/70" : "bg-[repeating-linear-gradient(180deg,var(--muted-foreground)_0_4px,transparent_4px_8px)] opacity-40")} />
                    </span>
                    <span className={cn("flex items-center gap-1 text-[11px]", link.secure === false ? "text-destructive" : link.secure ? "text-primary" : "text-muted-foreground")}>
                      <L size={12} weight="bold" />
                      {link.label}
                    </span>
                  </div>
                </TooltipTrigger>
                <TooltipContent className="max-w-64 text-xs">{link.note}</TooltipContent>
              </Tooltip>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export const hopIcons = { phone: DeviceMobile, provider: Buildings, sip: Cloud, room: Users, agent: Robot }
