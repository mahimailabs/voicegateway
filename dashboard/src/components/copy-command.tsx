import { useState } from "react"
import { Check, Copy } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"

/** A shell command to run on the server, wrapped to the width it has, with a button that copies it. */
export function CopyCommand({ text, label, copyLabel = "Copy command" }: { text: string; label: string; copyLabel?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative rounded-lg bg-foreground py-3 pr-12 pl-4 text-left font-mono text-[12px] leading-relaxed break-all whitespace-pre-wrap text-background">
      <code aria-label={label}>{text}</code>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="absolute top-2 right-2 text-background/70 hover:bg-background/10 hover:text-background"
        aria-label={copied ? "Copied" : copyLabel}
        onClick={() => {
          navigator.clipboard
            ?.writeText(text)
            .then(() => {
              setCopied(true)
              setTimeout(() => setCopied(false), 2000)
            })
            .catch(() => {
              // Clipboard is unavailable; the command stays selectable.
            })
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </div>
  )
}
