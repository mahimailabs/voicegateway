import { motion, useReducedMotion } from "motion/react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// The setup journey's type and surfaces, from the theme tokens (DESIGN.md): large headlines, a muted
// subtitle, one blue pill per screen, grouped grey surfaces with large radii, and a rise-in on entry
// that is skipped under prefers-reduced-motion.

export function Rise({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  const reduced = useReducedMotion()
  return (
    <motion.div className={className} initial={reduced ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}>
      {children}
    </motion.div>
  )
}

export function Eyebrow({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "success" }) {
  return <p className={cn("text-[15px] font-semibold sm:text-[17px]", tone === "success" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground")}>{children}</p>
}

export function Title({ children }: { children: React.ReactNode }) {
  return <h1 className="text-[36px] leading-[1.08] font-bold tracking-[-0.025em] text-balance sm:text-[56px]">{children}</h1>
}

export function Subtitle({ children }: { children: React.ReactNode }) {
  return <p className="max-w-[40ch] text-[17px] leading-snug text-pretty text-muted-foreground sm:text-[20px]">{children}</p>
}

export function Surface({ children, className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("rounded-[22px] bg-muted p-5 sm:p-6", className)} {...props}>
      {children}
    </div>
  )
}

export function Pill({ className, ...props }: React.ComponentProps<typeof Button>) {
  return <Button className={cn("h-12 rounded-full px-7 text-[17px] font-medium", className)} {...props} />
}
