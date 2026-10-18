import { useEffect, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { acceptInvitation, lookInvitation, type AcceptResult, type LookResult } from "@/lib/invitation"

export const INVALID = "This invitation is no longer valid. Ask an admin for a new link."
export const EXISTING_ACCOUNT = "If you already have an account with this email, use its password."

function message(r: AcceptResult): string | null {
  switch (r.kind) {
    case "invalid":
      return INVALID
    case "wrong":
      return "That password does not match the account with this email."
    case "too-short":
      return "Use at least 12 characters."
    case "limited":
      return r.retryAfterSeconds === null ? "Too many failed sign-ins. Try again later." : `Too many failed sign-ins. Try again in ${Math.ceil(r.retryAfterSeconds / 60)} min.`
    case "https-required":
      return "HTTPS is required to sign in when the console is not on localhost. Put VoiceGateway behind an HTTPS proxy."
    case "failed":
      return "VoiceGateway did not answer. Try again."
    default:
      return null
  }
}

/** Takes the token from the address's fragment, once, and drops it from the address bar, so it
 *  stays out of history, bookmarks and screenshots. Called before the page renders. */
export function readInviteToken(): string {
  const token = window.location.hash.replace(/^#/, "")
  if (token) window.history.replaceState(null, "", window.location.pathname + window.location.search)
  return token
}

/** The page an invitation or a password reset link opens (/invite#<token>). */
export function InvitePage({ token, onAccepted }: { token: string; onAccepted: () => void }) {
  const [look, setLook] = useState<LookResult | null>(token ? null : { kind: "invalid" })
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (token) void lookInvitation(token).then(setLook)
  }, [token])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    const result = await acceptInvitation(token, password)
    setBusy(false)
    if (result.kind === "accepted") {
      onAccepted()
      return
    }
    setError(message(result))
  }

  const pending = look?.kind === "pending" ? look.invitation : null
  const reset = pending?.kind === "reset"
  return (
    <main className="flex min-h-svh items-center justify-center bg-muted p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{reset ? "Set a new password" : pending ? `Join ${pending.organisationName}` : "VoiceGateway invitation"}</CardTitle>
          {pending && <CardDescription>{reset ? `For ${pending.email}. Setting it signs you out everywhere else.` : `As ${pending.role} for ${pending.email}.`}</CardDescription>}
        </CardHeader>
        <CardContent>
          {look === null && <p className="text-sm text-muted-foreground">Reading the invitation</p>}
          {look?.kind === "invalid" && (
            <p role="alert" className="text-sm text-destructive">
              {INVALID}
            </p>
          )}
          {look?.kind === "failed" && (
            <p role="alert" className="text-sm text-destructive">
              VoiceGateway did not answer. Reload to try again.
            </p>
          )}
          {pending && (
            <form onSubmit={submit}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="password">{reset ? "New password" : "Password"}</FieldLabel>
                  <Input
                    id="password"
                    type="password"
                    autoComplete={reset ? "new-password" : "current-password"}
                    required
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value)
                      setError(null)
                    }}
                  />
                  <FieldDescription>{reset ? "At least 12 characters." : `At least 12 characters. ${EXISTING_ACCOUNT}`}</FieldDescription>
                </Field>
                {error && (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                )}
                <Button type="submit" disabled={busy}>
                  {busy ? "Signing in" : reset ? "Set password" : "Join"}
                </Button>
              </FieldGroup>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  )
}
