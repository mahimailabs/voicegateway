import { useState } from "react"
import { Eye, EyeSlash } from "@phosphor-icons/react"
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group"

/** A password field with a button that shows what was typed, so a long password can be checked. */
export function PasswordInput({ id, autoComplete, value, onChange, describedBy }: { id: string; autoComplete: "current-password" | "new-password"; value: string; onChange: (value: string) => void; describedBy?: string }) {
  const [shown, setShown] = useState(false)
  return (
    <InputGroup className="h-9">
      <InputGroupInput id={id} type={shown ? "text" : "password"} autoComplete={autoComplete} required value={value} aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)} />
      <InputGroupAddon align="inline-end">
        <InputGroupButton size="icon-xs" aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown} onClick={() => setShown((s) => !s)}>
          {shown ? <EyeSlash /> : <Eye />}
        </InputGroupButton>
      </InputGroupAddon>
    </InputGroup>
  )
}
