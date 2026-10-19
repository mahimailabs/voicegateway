import { useMemo, useState } from "react"
import { AudioPlayerProvider } from "@/components/elevenlabs/audio-player"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Unknown } from "@/components/unknown"
import type { Call } from "@/lib/api"
import { type CallModel, buildModel, callLength, fmtMs, interruptionCount, median, turnCount } from "./call-model"
import { CallScript } from "./call-script"
import { CallPathStrip } from "./call-path-strip"
import { CallTimeline, clock } from "./call-timeline"
import { FindingsList, TurnDetail } from "./call-findings"
import { EventLog } from "./event-log"
import { TraceChart } from "./trace-chart"
import { useIsPhone } from "@/hooks/use-mobile"
import { useUrlState } from "@/lib/url-state"

type Speaker = "caller" | "agent"
// What the person is looking at: a turn, and optionally which side of it.
export type Focus = { turn: number; speaker: Speaker | null } | null

// The call in two short lines: how long and how fast it answered, then how it went and how it ended.
// The models live on the Call path tab; the full end reason is in the events.
function Verdict({ call, model }: { call: Call; model: CallModel }) {
  const responses = model.turns.map((t) => t.responseMs).filter((v): v is number => v !== null)
  const med = median(responses)
  const length = callLength(call, model)
  // The same count as the Calls list: unknown stays unknown, never 0.
  const turns = turnCount(call, model)
  const derived = model.turns.some((t) => t.responseDerived)
  const cut = model.source === "events" ? interruptionCount(model) : null
  const ended = call.endReason
    ? call.endReason
        .split(";")[0]
        .replace(/ after \d[\s\S]*$/, "")
        .trim()
    : null
  const facts = [cut !== null ? (cut === 0 ? "Nobody cut off" : `${cut} ${cut === 1 ? "reply" : "replies"} cut off`) : null, ended ? `Ended: ${ended}` : null].filter(Boolean)
  return (
    <div className="space-y-1 px-6 pt-4">
      <p className="text-[22px] leading-tight font-semibold tracking-[-0.02em] text-balance">
        {turns ?? <span className="text-muted-foreground">?</span>} {turns === 1 ? "turn" : "turns"} in {length !== null ? clock(length) : <Unknown why="Neither the room nor the call's timeline reported a length" />}.{" "}
        {med !== null && (
          <span className="text-muted-foreground">
            Answers in {fmtMs(med)}
            {derived ? " (derived)" : ""}.
          </span>
        )}
      </p>
      {facts.length > 0 && <p className="text-[13px] text-muted-foreground">{facts.join(" · ")}</p>}
    </div>
  )
}

export function CallView({ call }: { call: Call }) {
  const phone = useIsPhone()
  const model = useMemo(() => buildModel(call), [call])
  // The selected turn is in the URL as ?turn=3 or ?turn=3-agent (turns count from 1 there).
  const [turnParam, setTurnParam] = useUrlState<string | number | null>("turn", null)
  const m = /^(\d+)(?:-(caller|agent))?$/.exec(String(turnParam ?? ""))
  const focus: Focus = m ? { turn: Number(m[1]) - 1, speaker: (m[2] as Speaker | undefined) ?? null } : null
  const setFocus = (f: Focus) => setTurnParam(f ? (f.speaker ? `${f.turn + 1}-${f.speaker}` : f.turn + 1) : null)
  const [pane, setPane] = useUrlState<"insight" | "path" | "events" | "trace">("pane", "insight")
  // Zoom follows the timeline only; picking a message or a finding just moves the playhead there.
  const [zoom, setZoom] = useState<number | null>(null)
  const zoomTo = (i: number | null) => {
    setZoom(i)
    select(i)
  }
  const select = (i: number | null, speaker: Speaker | null = null) => {
    setFocus(i === null ? null : { turn: i, speaker })
    if (i !== null) setPane("insight")
  }

  // The conversation is the centre: transcript and its reading side by side on top, the recording
  // underneath. All three panes resize; the drawer keeps a margin under the recording.
  return (
    <AudioPlayerProvider>
      <div className="flex h-full min-h-0 flex-col">
        {call.recording?.preview && <p className="border-b bg-accent px-6 py-2 text-xs text-accent-foreground">{call.recording.preview}</p>}
        <Verdict call={call} model={model} />
        <ResizablePanelGroup orientation="vertical" className="mt-4 min-h-0 flex-1 border-t">
          <ResizablePanel defaultSize="68%" minSize="30%">
            {/* On a phone the transcript sits above its reading instead of beside it. */}
            <ResizablePanelGroup key={phone ? "stack" : "side"} orientation={phone ? "vertical" : "horizontal"}>
              <ResizablePanel defaultSize="52%" minSize="30%" className="min-h-0">
                <div className="h-full overflow-y-auto">
                  <CallScript call={call} model={model} selected={focus} onSelect={(i, who) => (setZoom(null), select(i, who))} />
                </div>
              </ResizablePanel>
              <ResizableHandle withHandle className="after:w-3 aria-[orientation=horizontal]:after:h-3" />
              <ResizablePanel defaultSize="48%" minSize="28%" className="min-h-0">
                <div className="h-full overflow-y-auto">
                  <Tabs value={pane} onValueChange={(v) => setPane(v as typeof pane)} className="gap-0">
                    <div className="sticky top-0 z-10 overflow-x-auto border-b bg-background/95 px-4 py-2.5 backdrop-blur-sm sm:px-6">
                      <TabsList>
                        <TabsTrigger value="insight">{focus ? `Turn ${focus.turn + 1}${focus.speaker ? ` · ${focus.speaker === "caller" ? "Caller" : "Agent"}` : ""}` : "Insights"}</TabsTrigger>
                        <TabsTrigger value="path">Call path</TabsTrigger>
                        <TabsTrigger value="events" disabled={!call.events?.length}>
                          Events
                        </TabsTrigger>
                        <TabsTrigger value="trace" disabled={!call.spans?.length}>
                          Trace
                        </TabsTrigger>
                      </TabsList>
                    </div>
                    <TabsContent key={pane} value={pane}>
                      {pane === "path" ? (
                        <div className="pb-5">
                          <CallPathStrip call={call} model={model} onModels={call.spans?.length ? () => setPane("trace") : null} />
                        </div>
                      ) : (
                        <div className="px-4 py-5 sm:px-6">
                          {pane === "insight" && (focus ? <TurnDetail call={call} model={model} turn={focus.turn} speaker={focus.speaker} onBack={() => setFocus(null)} /> : <FindingsList call={call} model={model} onTurn={select} />)}
                          {pane === "events" && <EventLog call={call} model={model} onSelect={select} />}
                          {pane === "trace" && call.spans && <TraceChart spans={call.spans} focus={focus} />}
                        </div>
                      )}
                    </TabsContent>
                  </Tabs>
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          </ResizablePanel>
          <ResizableHandle withHandle className="after:w-3 aria-[orientation=horizontal]:after:h-3" />
          <ResizablePanel defaultSize="32%" minSize="200px" className="min-h-0">
            <div className="h-full overflow-y-auto bg-muted/30 pb-8">
              <CallTimeline call={call} model={model} selected={focus?.turn ?? null} zoom={zoom} onSelect={zoomTo} />
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </AudioPlayerProvider>
  )
}
