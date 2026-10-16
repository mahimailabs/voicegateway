import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Unknown } from "@/components/unknown"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { Call, SessionEvent } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { CallModel } from "./call-model"
import { useUrlState } from "@/lib/url-state"

// The agent's session events in order, like LiveKit's console, filterable by kind.
const groups = {
  all: "All",
  state: "States",
  speech: "Transcripts",
  turn: "Turn taking",
  items: "Messages",
} as const
type Group = keyof typeof groups

const groupOf = (e: SessionEvent): Group => (e.type === "user_state" || e.type === "agent_state" ? "state" : e.type === "transcript" ? "speech" : e.type === "item" ? "items" : "turn")

function describe(e: SessionEvent) {
  switch (e.type) {
    case "user_state":
      return { kind: "Caller state", text: `${e.from} → ${e.to}` }
    case "agent_state":
      return { kind: "Agent state", text: `${e.from} → ${e.to}` }
    case "transcript":
      return { kind: e.final ? "Final transcript" : "Partial transcript", text: `"${e.text}"` }
    case "end_of_turn":
      return { kind: "End of turn", text: `probability ${e.probability?.toFixed(2) ?? "unknown"}, threshold ${e.threshold?.toFixed(2) ?? "unknown"}` }
    case "overlapping_speech":
      return { kind: "Overlapping speech", text: `${e.isInterruption ? "interruption" : "backchannel"}, detected in ${e.detectionDelay !== null ? `${Math.round(e.detectionDelay * 1000)} ms` : "an unreported time"}` }
    case "item":
      return { kind: "Message added", text: e.role }
  }
}

export function EventLog({ call, model, onSelect }: { call: Call; model: CallModel; onSelect: (i: number) => void }) {
  const [group, setGroup] = useUrlState<Group>("events", "all")
  const events = call.events ?? []
  if (!events.length) {
    return (
      <Empty className="py-12">
        <EmptyHeader>
          <EmptyTitle className="text-sm">No session events</EmptyTitle>
          <EmptyDescription className="text-xs">This call carried no agent session events. They arrive with LiveKit's observability upload (decision 0014).</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }
  const turnAt = (t: number) => model.turns.findIndex((x) => t >= x.window.start && t <= x.window.end)
  const shown = events.filter((e) => group === "all" || groupOf(e) === group)
  return (
    <div className="space-y-3">
      <ToggleGroup type="single" variant="outline" size="sm" value={group} onValueChange={(v) => v && setGroup(v as Group)}>
        {Object.entries(groups).map(([k, label]) => (
          <ToggleGroupItem key={k} value={k} className="px-2.5 text-xs">
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-20">Time</TableHead>
            <TableHead className="w-40">Event</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((e, k) => {
            const d = describe(e)
            const turn = e.t !== null ? turnAt(e.t) : -1
            return (
              <TableRow key={k} className={cn(turn >= 0 && "cursor-pointer")} onClick={() => turn >= 0 && onSelect(turn)}>
                <TableCell className="text-muted-foreground tnum">{e.t !== null ? `${e.t.toFixed(2)} s` : <Unknown why="No time reported for this event" />}</TableCell>
                <TableCell className={cn(e.type === "overlapping_speech" && (e.isInterruption ? "text-destructive" : "font-medium"))}>{d.kind}</TableCell>
                <TableCell className="max-w-0 truncate text-muted-foreground">{d.text}</TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
