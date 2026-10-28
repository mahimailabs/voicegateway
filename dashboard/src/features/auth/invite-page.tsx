import { useEffect, useState, type FormEvent } from "react"
import { WarningCircle } from "@phosphor-icons/react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { AuthShell } from "@/features/auth/auth-shell"
import { PasswordInput } from "@/features/auth/password-input"
import { ProviderButtons } from "@/features/auth/provider-buttons"
import { acceptInvitation, lookInvitation, type AcceptResult, type LookResult } from "@/lib/invitation"

export const INVALID = "This invitation is no longer valid. Ask an admin for a new link."
// After a reload the address holds no token: this page took it out of the address bar.
export const NO_TOKEN = "This page holds no invitation. Open the link from your email or your admin again."
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
  const [look, setLook] = useState<LookResult | null>(null)
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
    <AuthShell
      title={reset ? "Set a new password" : pending ? `Join ${pending.organisationName}` : "VoiceGateway invitation"}
      description={pending ? (reset ? `For ${pending.email}. Setting it signs you out everywhere else.` : `As ${pending.role} for ${pending.email}.`) : undefined}
    >
      {!token && <Problem>{NO_TOKEN}</Problem>}
      {token && look === null && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner />
          Reading the invitation
        </p>
      )}
      {look?.kind === "invalid" && <Problem>{INVALID}</Problem>}
      {look?.kind === "failed" && <Problem>VoiceGateway did not answer. Reload to try again.</Problem>}
      {pending && (
        <form onSubmit={submit}>
          <FieldGroup>
            {!reset && <ProviderButtons invitationToken={token} onError={setError} />}
            <Field>
              <FieldLabel htmlFor="password">{reset ? "New password" : "Password"}</FieldLabel>
              <PasswordInput
                id="password"
                autoComplete={reset ? "new-password" : "current-password"}
                describedBy="password-help"
                value={password}
                onChange={(v) => {
                  setPassword(v)
                  setError(null)
                }}
              />
              <FieldDescription id="password-help">{reset ? "At least 12 characters." : `At least 12 characters. ${EXISTING_ACCOUNT}`}</FieldDescription>
            </Field>
            {error && <Problem>{error}</Problem>}
            <Button type="submit" size="lg" disabled={busy}>
              {busy && <Spinner />}
              {busy ? "Signing in" : reset ? "Set password" : "Join"}
            </Button>
          </FieldGroup>
        </form>
      )}
    </AuthShell>
  )
}

function Problem({ children }: { children: string }) {
  return (
    <Alert variant="destructive">
      <WarningCircle />
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  )
}
