import { useState, type FormEvent } from "react"
import { CopyCommand } from "@/components/copy-command"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
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
    <main className="flex min-h-svh items-center justify-center bg-muted p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to VoiceGateway</CardTitle>
          {needsAdmin ? (
            <CardDescription>No account exists yet. Create the first admin on the server, then sign in.</CardDescription>
          ) : (
            <CardDescription>Use your VoiceGateway email and password.</CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {needsAdmin && (
            <div className="mb-4">
              <CopyCommand text={FIRST_ADMIN_COMMAND} label="First admin command" />
            </div>
          )}
          <form onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="email">Email</FieldLabel>
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    setError(null)
                  }}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value)
                    setError(null)
                  }}
                />
              </Field>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <Button type="submit" disabled={busy}>
                {busy ? "Signing in" : "Sign in"}
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
