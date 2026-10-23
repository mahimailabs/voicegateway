import { useEffect, useState } from "react"
import { GithubLogo, GoogleLogo } from "@phosphor-icons/react"
import { siAuth0 } from "simple-icons"
import { Button } from "@/components/ui/button"
import { FieldSeparator } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { fetchProviders, NO_PROVIDERS, startProvider, type ProviderName, type Providers } from "@/lib/providers"

const LABELS: Record<ProviderName, string> = { google: "Google", github: "GitHub", auth0: "Auth0" }

// Auth0's mark from simple-icons, in the button's text colour like the Phosphor logos; weight is
// theirs, ignored.
function Auth0Logo(_: { weight?: "bold" }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor">
      <path d={siAuth0.path} />
    </svg>
  )
}
const ICONS = { google: GoogleLogo, github: GithubLogo, auth0: Auth0Logo }

/**
 * "Continue with Google", "Continue with GitHub" and "Continue with Auth0", for the providers the server has on, above an
 * "or" divider; nothing at all when none is on. With invitationToken, the provider accepts that
 * invitation. onError gets a line to show when the sign-in cannot start.
 */
export function ProviderButtons({ invitationToken, onError }: { invitationToken?: string; onError: (message: string) => void }) {
  const [providers, setProviders] = useState<Providers>(NO_PROVIDERS)
  const [busy, setBusy] = useState<ProviderName | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    void fetchProviders(abort.signal).then((p) => {
      if (!abort.signal.aborted) setProviders(p)
    })
    return () => abort.abort()
  }, [])

  const on = (Object.keys(LABELS) as ProviderName[]).filter((p) => providers[p])
  if (on.length === 0) return null

  async function go(provider: ProviderName) {
    setBusy(provider)
    const result = await startProvider(provider, invitationToken)
    if (result.kind === "redirect") {
      window.location.assign(result.url)
      return
    }
    setBusy(null)
    onError(
      result.kind === "https-required"
        ? "HTTPS is required to sign in when the console is not on localhost. Put VoiceGateway behind an HTTPS proxy."
        : "VoiceGateway did not answer. Try again.",
    )
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        {on.map((p) => {
          const Icon = ICONS[p]
          return (
            <Button key={p} type="button" variant="outline" size="lg" disabled={busy !== null} onClick={() => void go(p)}>
              {busy === p ? <Spinner /> : <Icon weight="bold" />}
              Continue with {LABELS[p]}
            </Button>
          )
        })}
      </div>
      <FieldSeparator>or</FieldSeparator>
    </>
  )
}
