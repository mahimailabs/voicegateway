import { cn } from "@/lib/utils"

// One divided strip of measurements under a page title (DESIGN.md, Containers): numbers in ink,
// hairlines between them, no card around each. A measurement that needs a look gets a small red dot
// beside its label; the number itself keeps its colour, so red stays rare.
export function StatStrip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-x-5 border-y sm:auto-cols-fr sm:grid-flow-col sm:gap-x-0 sm:divide-x", className)}>
      {children}
    </div>
  )
}

export function Stat({ value, label, children, alert }: { value: React.ReactNode; label: string; children?: React.ReactNode; alert?: boolean }) {
  return (
    <div className="min-w-0 py-4 sm:px-5 sm:first:pl-0 sm:last:pr-0">
      <p className="flex items-center gap-1.5 text-footnote text-muted-foreground">
        {label}
        {alert && (
          <span className="size-1.5 shrink-0 rounded-full bg-destructive" aria-label="needs a look" role="img" />
        )}
      </p>
      <p className="mt-1 text-title-1 font-semibold tnum">{value}</p>
      {children && <div className="mt-2 text-caption text-muted-foreground">{children}</div>}
    </div>
  )
}
