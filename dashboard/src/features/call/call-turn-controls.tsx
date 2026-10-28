import { useMemo } from "react"
import { WheelPicker, WheelPickerWrapper } from "@ncdai/react-wheel-picker"
import "@ncdai/react-wheel-picker/style.css"
import { Button } from "@/components/ui/button"
import { useUrlState } from "@/lib/url-state"
import type { Call } from "@/lib/api"
import { buildModel } from "./call-model"

export function CallTurnControls({ call }: { call: Call }) {
  const model = useMemo(() => buildModel(call), [call])
  const slowest = model.turns.reduce<typeof model.turns[number] | undefined>((best, t) => t.responseMs !== null && (best?.responseMs == null || t.responseMs > best.responseMs) ? t : best, undefined)
  const [view] = useUrlState("callView", "conversation")
  const [selected, setSelected] = useUrlState("diagnosisTurn", (slowest?.i ?? model.turns[0]?.i ?? 0) + 1)
  const turn = model.turns.find((t) => t.i === Number(selected) - 1) ?? slowest ?? model.turns[0]
  if (view === "conversation" || !turn) return null
  return <div className="flex shrink-0 items-center gap-2 border-l pl-2" aria-label="Diagnosis turn controls">
    {slowest && <Button size="sm" variant="ghost" onClick={() => setSelected(slowest.i + 1)}>Slowest</Button>}
    <div className="w-28" aria-label="Scroll to select a turn"><WheelPickerWrapper><WheelPicker options={model.turns.map((t) => ({ value: t.i + 1, label: `Turn ${t.i + 1}` }))} value={turn.i + 1} onValueChange={setSelected} visibleCount={8} optionItemHeight={24} classNames={{ optionItem: "text-muted-foreground text-sm tabular-nums", highlightWrapper: "bg-accent rounded-xl", highlightItem: "text-foreground text-sm font-semibold tabular-nums" }} /></WheelPickerWrapper></div>
  </div>
}
