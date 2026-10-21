import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Key } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { LiveKitPaste } from "@/components/livekit-paste"
import { errorText } from "@/components/page-state"
import { connectionQuery, updateLiveKitCredentials } from "@/lib/api"
import { type LiveKitEnv, NOTHING_PASTED, filledText, namesLiveKit, pasteLiveKit } from "@/lib/livekit-env"
import { currentScope } from "@/lib/url-state"

// The same LiveKit server written another way (wss and https, a trailing slash) is the same project.
function sameServer(a: string, b: string) {
  try {
    const x = new URL(a.trim())
    const y = new URL(b.trim())
    return x.host.toLowerCase() === y.host.toLowerCase() && x.pathname.replace(/\/+$/, "") === y.pathname.replace(/\/+$/, "")
  } catch {
    return a.trim().replace(/\/+$/, "") === b.trim().replace(/\/+$/, "")
  }
}

// Replaces the project's LiveKit API key and secret (updateProjectLiveKitCredentials), for an Admin;
// callers show it to Admins only. The URL never changes: one VoiceGateway project is one LiveKit
// project, so a pasted URL is only compared with this project's. web checks the pair with one LiveKit
// read before it seals the secret. The secret is held only until it is sent and cleared after every
// submit; a change reads the connection again so the shown key is the new one.
export function ChangeLiveKitKey({ url }: { url: string }) {
  const client = useQueryClient()
  const [key, setKey] = useState("")
  const [secret, setSecret] = useState("")
  const [note, setNote] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const change = useMutation({
    mutationFn: ({ k, s }: { k: string; s: string }) => updateLiveKitCredentials(currentScope(), k, s),
    onSuccess: () => {
      setKey("")
      setNote(null)
      setDone(true)
      return client.invalidateQueries({ queryKey: connectionQuery().queryKey })
    },
    onError: (e: Error) => setProblem(errorText(e)),
  })
  const fill = (env: LiveKitEnv) => {
    if (!namesLiveKit(env)) return setNote(NOTHING_PASTED)
    if (env.apiKey !== undefined) setKey(env.apiKey)
    if (env.apiSecret !== undefined) setSecret(env.apiSecret)
    const other = env.url !== undefined && !sameServer(env.url, url)
    const filled = env.apiKey !== undefined || env.apiSecret !== undefined ? filledText(env, ["apiKey", "apiSecret"]) : ""
    setNote([filled, other ? `This project is ${url}; a different LiveKit project needs its own VoiceGateway project.` : ""].filter(Boolean).join(" ") || null)
  }
  const onPaste = pasteLiveKit(fill)
  const ready = key.trim() !== "" && secret.trim() !== "" && !change.isPending
  return (
    <form
      aria-label="Change key and secret"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ready) return
        setProblem(null)
        setDone(false)
        change.mutate({ k: key.trim(), s: secret })
        setSecret("")
      }}
    >
      <FieldGroup className="gap-4">
        <LiveKitPaste id="livekit-change-paste" onParsed={fill} note={note} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="livekit-change-key">New API key</FieldLabel>
            <Input id="livekit-change-key" value={key} onChange={(e) => setKey(e.target.value)} onPaste={onPaste} autoComplete="off" spellCheck={false} />
          </Field>
          <Field>
            <FieldLabel htmlFor="livekit-change-secret">New API secret</FieldLabel>
            <Input id="livekit-change-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} onPaste={onPaste} autoComplete="new-password" />
          </Field>
        </div>
        <FieldDescription>Agents and LiveKit webhooks must sign with this key; update their LIVEKIT_API_KEY and LIVEKIT_API_SECRET after changing it.</FieldDescription>
        {problem && <FieldError>{problem}</FieldError>}
        {done && (
          <p role="status" className="text-[13px] text-muted-foreground">
            Key and secret changed.
          </p>
        )}
        <div>
          <Button type="submit" size="sm" disabled={!ready}>
            {change.isPending ? <Spinner /> : <Key />} Change key and secret
          </Button>
        </div>
      </FieldGroup>
    </form>
  )
}
