import type { Call } from "@/lib/api"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

export function CallUsage({ call }: { call: Call }) {
  const u = call.usage
  const rows = [
    ["LLM input", u?.llmInputTokens, "tokens"],
    ["LLM output", u?.llmOutputTokens, "tokens"],
    ["Cached input (included above)", u?.llmCachedTokens, "tokens"],
    ["STT audio", u?.sttSeconds, "seconds"],
    ["TTS text", u?.ttsCharacters, "characters"],
    ["TTS audio", u?.ttsSeconds, "seconds"],
  ] as const
  return <section className="space-y-2" aria-label="Reported call usage">
    <h3 className="text-[15px] font-semibold">Reported usage</h3>
    <Table tabIndex={0} aria-label="Reported call usage">
      <TableHeader><TableRow><TableHead>Measurement</TableHead><TableHead className="text-right">Total</TableHead></TableRow></TableHeader>
      <TableBody>{rows.map(([label, value, unit]) => <TableRow key={label}>
        <TableCell>{label}</TableCell><TableCell className="text-right tnum">{value == null ? "Unknown" : `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })} ${unit}`}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <p className="text-xs text-muted-foreground">{u?.note ?? "No complete usage totals were reported. Unknown is not zero."}</p>
  </section>
}
