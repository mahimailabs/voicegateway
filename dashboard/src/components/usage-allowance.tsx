import { useQuery } from "@tanstack/react-query"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { ApiError, entitlementQuery } from "@/lib/api"

// This API is absent on self-hosted installs. Unknown usage is never rendered as zero.
export function UsageAllowance() {
  const query = useQuery(entitlementQuery())
  if (query.error instanceof ApiError && query.error.status === 404) return null
  if (query.isPending) return null
  if (query.isError) return <Alert><AlertTitle>Usage unavailable</AlertTitle><AlertDescription>We could not check your allowance. Try again shortly.</AlertDescription></Alert>
  const usage = query.data
  if (!usage) return null
  return <Alert variant={usage.ingestAllowed ? "default" : "destructive"}>
    <AlertTitle>{!usage.ingestAllowed ? "Telemetry ingestion paused" : usage.warningPercent >= 80 ? `${usage.warningPercent}% of allowance used` : "Profiled call usage"}</AlertTitle>
    <AlertDescription>
      <p>{Math.floor(usage.callSeconds / 60).toLocaleString()} / {usage.limitMinutes.toLocaleString()} minutes for {new Date(usage.from).toLocaleDateString()} to {new Date(usage.to).toLocaleDateString()}.</p>
      <p>Closed calls are counted. In-flight calls may take usage over the allowance. No automatic overage charges. Existing data stays readable and your voice calls continue.</p>
      <a href="https://voicegateway.dev/support" target="_blank" rel="noreferrer">Request more capacity</a>
    </AlertDescription>
  </Alert>
}
