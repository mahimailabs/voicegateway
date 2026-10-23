import { useState, type FormEvent } from "react"
import { WarningCircle } from "@phosphor-icons/react"
import { CopyCommand } from "@/components/copy-command"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { AuthShell } from "@/features/auth/auth-shell"
import { PasswordInput } from "@/features/auth/password-input"
import { signIn, type SignInResult } from "@/lib/session"

// The command that creates an install's first admin (decision 0027). There is no open sign-up.
export const FIRST_ADMIN_COMMAND = "docker compose exec web voicegateway user add --admin --email you@example.com"

function message(r: SignInResult): string | null {
  switch (r.kind) {
    case "wrong":
      return "Email or password is wrong."
    case "limited":
      return r.retryAfterSeconds === null
        ? "Too many failed sign-ins. Try again later."
        : `Too many failed sign-ins. Try again in ${Math.ceil(r.retryAfterSeconds / 60)} min.`
    case "https-required":
      return "HTTPS is required to sign in when the console is not on localhost. Put VoiceGateway behind an HTTPS proxy."
    case "failed":
      return "VoiceGateway did not answer. Try again."
    default:
      return null
  }
}

/** The sign-in screen the console shows when no one is signed in and the mock API is off. */
export function SignInScreen({ needsAdmin, onSignedIn }: { needsAdmin: boolean; onSignedIn: () => void }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    const result = await signIn(email, password)
    setBusy(false)
    if (result.kind === "signed-in") {
      onSignedIn()
      return
    }
    setError(message(result))
  }

  return (
    <AuthShell
      title={needsAdmin ? "Create the first admin" : "Sign in to VoiceGateway"}
      description={needsAdmin ? "No account exists yet. Create the first admin on the server, then sign in." : "Use your VoiceGateway email and password."}
      footer={needsAdmin ? undefined : "Need an account? Ask an admin of your organisation for an invitation."}
    >
      {needsAdmin && <CopyCommand text={FIRST_ADMIN_COMMAND} label="First admin command" />}
      <form onSubmit={submit}>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="email">Email</FieldLabel>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="you@company.com"
              className="h-9"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                setError(null)
              }}
            />
          </Field>
          <Field>
            <div className="flex items-center justify-between gap-2">
              <FieldLabel htmlFor="password">Password</FieldLabel>
              {!needsAdmin && (
                <Popover>
                  <PopoverTrigger asChild>
                    <Button type="button" variant="link" size="xs" className="h-auto px-0 text-muted-foreground">
                      Forgot password?
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="end" className="w-72">
                    <PopoverHeader>
                      <PopoverTitle>Ask an admin for a reset link</PopoverTitle>
                      <PopoverDescription>An admin of your organisation can send you one from Settings, Members. It works for 24 hours.</PopoverDescription>
                    </PopoverHeader>
                  </PopoverContent>
                </Popover>
              )}
            </div>
            <PasswordInput
              id="password"
              autoComplete="current-password"
              value={password}
              onChange={(v) => {
                setPassword(v)
                setError(null)
              }}
            />
          </Field>
          {error && (
            <Alert variant="destructive">
              <WarningCircle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button type="submit" size="lg" disabled={busy}>
            {busy && <Spinner />}
            {busy ? "Signing in" : "Sign in"}
          </Button>
        </FieldGroup>
      </form>
    </AuthShell>
  )
}
