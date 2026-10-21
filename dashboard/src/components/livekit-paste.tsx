import { useState } from "react"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Textarea } from "@/components/ui/textarea"
import { type LiveKitEnv, parseLiveKitEnv } from "@/lib/livekit-env"

// A paste target for LiveKit Cloud's "Environment variables" block (Settings, API keys, Create key).
// The pasted text never stays in the box: what it names goes to onParsed, which fills the form's own
// inputs (the secret only into a password input), and the box is emptied at once. onParsed also hears
// a paste that names none of the three, so the form can say so in its note.
export function LiveKitPaste({ id, onParsed, note }: { id: string; onParsed: (env: LiveKitEnv) => void; note?: React.ReactNode }) {
  const [draft, setDraft] = useState("")
  const take = (text: string) => {
    setDraft("")
    onParsed(parseLiveKitEnv(text))
  }
  return (
    <Field>
      <FieldLabel htmlFor={id}>Paste from LiveKit</FieldLabel>
      <Textarea
        id={id}
        rows={3}
        className="min-h-20 font-mono text-[13px] md:text-[13px]"
        placeholder={"LIVEKIT_URL=wss://your-project.livekit.cloud\nLIVEKIT_API_KEY=...\nLIVEKIT_API_SECRET=..."}
        value={draft}
        autoComplete="off"
        spellCheck={false}
        onPaste={(e) => {
          e.preventDefault()
          take(e.clipboardData.getData("text"))
        }}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft.trim() !== "" && take(draft)}
      />
      <FieldDescription>In LiveKit Cloud: Settings, API keys, Create key, then copy Environment variables.</FieldDescription>
      <div role="status" className="empty:hidden text-[13px] text-muted-foreground">
        {note}
      </div>
    </Field>
  )
}
