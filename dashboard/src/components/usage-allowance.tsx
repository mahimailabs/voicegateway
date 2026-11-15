import { Button } from "@/components/ui/button"
import { useQuery } from "@tanstack/react-query"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { entitlementQuery } from "@/lib/api"
import type { OrganisationEntitlement } from "@/lib/api-types"
import { installOf } from "@/lib/hosted"
import { REQUEST_CAPACITY } from "@/lib/support"

const periodDate = new Intl.DateTimeFormat("en", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" })

// The plan's set limits, in one line; a null limit is unlimited and is left out, never shown as zero.
const many = (n: number, one: string) => `${n.toLocaleString()} ${one}${n === 1 ? "" : "s"}`
const planLimits = (e: OrganisationEntitlement) => [
  e.retentionDays === null ? null : `call data kept ${many(e.retentionDays, "day")}`,
  e.maxProjects === null ? null : `up to ${many(e.maxProjects, "project")}`,
  e.maxMembers === null ? null : `up to ${many(e.maxMembers, "member")}, open invitations counted`,
].filter(Boolean).join(", ").replace(/^./, (c) => c.toUpperCase()) || null

// This API is absent on self-hosted installs. Unknown usage is never rendered as zero.
export function UsageAllowance() {
  const query = useQuery(entitlementQuery())
  if (installOf(query) === "self-hosted") return null
  if (query.isPending) return null
  if (query.isError) return <Alert><AlertTitle>Usage unavailable</AlertTitle><AlertDescription>We could not check your allowance. Try again shortly.</AlertDescription></Alert>
  const usage = query.data
  if (!usage) return null
  const limits = planLimits(usage)
  return <Alert variant={usage.ingestAllowed ? "default" : "destructive"}>
    <AlertTitle>{!usage.ingestAllowed ? "Profiling paused" : usage.warningPercent >= 80 ? `${usage.warningPercent}% of credits used` : "Profiled call credits"}</AlertTitle>
    <AlertDescription>
      <p>{Math.floor(usage.callSeconds / 60).toLocaleString()} / {usage.limitCredits.toLocaleString()} credits for {periodDate.format(new Date(usage.from))} until {periodDate.format(new Date(usage.to))} UTC (end exclusive). One profiled call minute is one credit.</p>
      {!usage.ingestAllowed && <p>New calls are not profiled until the plan renews or adds credits. Calls are never blocked.</p>}
      {limits && <p>{limits}</p>}
      <p>{usage.openCalls.toLocaleString()} open calls and {usage.unmeasuredCalls.toLocaleString()} closed calls with unknown duration, first seen in this period, are excluded from measured usage.</p>
      <p>Measured closed calls are counted. In-flight calls may take usage over the allowance. No automatic overage charges. Existing data stays readable and your voice calls continue.</p>
      <Button variant="link" asChild><a href={REQUEST_CAPACITY.href} target="_blank" rel="noreferrer">{REQUEST_CAPACITY.label}</a></Button>
    </AlertDescription>
  </Alert>
}
