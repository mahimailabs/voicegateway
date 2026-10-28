import { useLiveConnection } from "@/components/live-updates"
import { useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { SourceStatus as Source } from "@/lib/api-types"
import { freshness } from "@/lib/live-status"

export function useObservationClock() {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}

export function SourceStatus({ name, source, interval, query }: {
  name: string; source?: Source; interval: number
  query: { isError: boolean; fetchStatus: string; refetch: () => unknown }
}) {
  const connection = useLiveConnection()
  const now = useObservationClock()
  const state = freshness(source, interval, now)
  const disconnected = query.isError || query.fetchStatus === "paused"
  const label = disconnected ? "API disconnected" : state === "current" ? "Updating" : state === "stale" ? "Stale samples" : state === "unavailable" ? "Source unavailable" : "Awaiting samples"
  return <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" role="status">
    <span>{name}</span>
    {connection && <Badge variant="outline">{connection === "connected" ? "Live feed connected" : connection === "paused" ? "Live feed paused" : connection === "connecting" ? "Connecting live feed" : "Live feed disconnected"}</Badge>}
    <Badge variant={disconnected || state !== "current" ? "outline" : "secondary"}>{label}</Badge>
    <span>{source?.lastGoodReadAt ? `Last observed ${new Date(source.lastGoodReadAt).toLocaleString()}` : "No successful observation"}</span>
    {source?.errorClass && <span>{source.errorClass}</span>}
    <span>· {interval / 1000} s sampling</span>
    {disconnected && <Button variant="ghost" size="sm" onClick={() => void query.refetch()}>Reconnect</Button>}
  </div>
}
