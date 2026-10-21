import { useState } from "react"
import { BracketsCurly, Robot, User } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import type { Call } from "@/lib/api"
import type { LiveState } from "@/lib/api-types"
import { relative } from "@/lib/relative-time"

// What a VoiceGateway SDK attached to the call (getCall's fields and live). A call no SDK attached to
// shows neither: no fields means nothing to list, and a null live state is unknown, not idle.

export function CallFields({ call }: { call: Pick<Call, "fields"> }) {
  const entries = Object.entries(call.fields ?? {}).sort(([a], [b]) => a.localeCompare(b))
  if (!entries.length) return null
  return (
    <ul aria-label="Custom fields" className="flex min-w-0 flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <li key={k} className="max-w-full min-w-0">
          <Badge variant="outline" className="max-w-full font-normal" title={`${k}: ${v}`}>
            <BracketsCurly data-icon="inline-start" />
            <span className="text-muted-foreground">{k}</span>
            <span className="truncate font-mono">{v}</span>
          </Badge>
        </li>
      ))}
    </ul>
  )
}

function LiveBadge({ who, s, now, ended }: { who: "User" | "Agent"; s: LiveState; now: number; ended: boolean }) {
  const at = new Date(s.at).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" })
  // An ended call's state is its last one: said with its time, never as happening now.
  const when = ended ? new Date(s.at).toLocaleTimeString([], { timeStyle: "medium" }) : relative(s.at, now)
  return (
    <li>
      <Badge variant={ended ? "outline" : "secondary"} className="font-normal" title={`${who} ${s.state}, ${ended ? "last reported" : "reported"} ${at}`}>
        {who === "User" ? <User data-icon="inline-start" /> : <Robot data-icon="inline-start" />}
        {`${who} ${s.state} · `}
        <time dateTime={s.at}>{when}</time>
      </Badge>
    </li>
  )
}

export function CallLive({ call, now: at }: { call: Pick<Call, "live"> & { endedAt?: Call["endedAt"] }; now?: number }) {
  // Times are relative to when the call was read into view, so a render never changes them.
  const [now] = useState(() => at ?? Date.now())
  const live = call.live
  if (!live || (!live.user && !live.agent)) return null
  const ended = call.endedAt != null
  return (
    <ul aria-label={ended ? "Last state" : "Live state"} className="flex flex-wrap items-center gap-1">
      {ended && <li className="text-xs text-muted-foreground">Last state:</li>}
      {live.user && <LiveBadge who="User" s={live.user} now={now} ended={ended} />}
      {live.agent && <LiveBadge who="Agent" s={live.agent} now={now} ended={ended} />}
    </ul>
  )
}
