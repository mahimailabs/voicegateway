import { useCallback, useState } from "react"
import { Navigate, useNavigate } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { BrandWordmark } from "@/components/brand-wordmark"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { connectionQuery, fetchWorkspaces } from "@/lib/api"
import type { ProjectCreated } from "@/lib/api-types"
import { setWorkspaces } from "@/lib/workspaces"
import { useHosted } from "@/lib/hosted"
import { roleIn, useSession } from "@/lib/session"
import { currentOrg, currentScope, scopeUnknown, useUrlState } from "@/lib/url-state"
import { cn } from "@/lib/utils"
import { ConnectScreen, ConnectedScreen, WelcomeScreen } from "./connect-screens"
import { AgentScreen, CallScreen, FirstLookScreen } from "./run-screens"
import { RAIL, type SetupStep, setupStep, viewedStep } from "./setup-model"
import { setUpLater } from "./setup-later"

// The Cloud setup journey, full screen at /setup: no sidebar, one idea per screen. The step comes from
// the API (setup-model.ts); ?step= holds a step the user went back to. Self-hosted installs keep the
// simple add-project form and never open this page.

// The rail: four dots with labels, the current one bold; dots up to the step on screen are blue and
// the rest grey. A step the data has reached can be opened again; a step ahead of the data cannot. A list, not tabs: it moves between screens.
export function Rail({ derived, viewed, onView }: { derived: 1 | 2 | 3 | 4; viewed: SetupStep; onView: (s: SetupStep) => void }) {
  const at = viewed === 0 ? 1 : viewed
  return (
    <nav aria-label="Setup steps">
      <ol className="flex items-center gap-3 sm:gap-5">
        {RAIL.map(({ step, label }) => {
          const reached = step <= derived
          const body = (
            <>
              <span className={cn("size-2 rounded-full", step <= at ? "bg-primary" : "bg-muted-foreground/30")} aria-hidden="true" />
              <span className={cn("text-[12px] sm:text-[13px]", step === at ? "font-semibold text-foreground" : "text-muted-foreground")}>{label}</span>
            </>
          )
          return (
            <li key={step} aria-current={step === at ? "step" : undefined}>
              {reached && step !== at ? (
                <button type="button" className="flex items-center gap-1.5 rounded-full px-1 py-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50" onClick={() => onView(step)}>
                  {body}
                </button>
              ) : (
                <span className="flex items-center gap-1.5 px-1 py-1">{body}</span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

export function SetupPage() {
  const navigate = useNavigate()
  const session = useSession()
  const install = useHosted()
  const org = currentOrg()
  const hasProject = !scopeUnknown()
  const read = useQuery({ ...connectionQuery(), enabled: hasProject })
  const derived = setupStep(hasProject, read.data, read.dataUpdatedAt)
  const [asked, setAsked] = useUrlState<number | null>("step", null)
  // The screen on view, chosen once the data can say where the project is; a screen that finished
  // moves on through onNext, so its success state is seen first.
  const ready = !hasProject || read.data !== undefined || read.isError
  const [current, setCurrent] = useState<SetupStep | null>(null)
  if (ready && current === null) setCurrent(viewedStep(derived, asked))
  const go = useCallback(
    (s: SetupStep) => {
      setCurrent(s)
      setAsked(s === derived || (s === 0 && derived === 1) ? null : s)
    },
    [derived, setAsked, setCurrent],
  )
  const toAgent = useCallback(() => go(3), [go])
  // The new project opens inside the app: the workspaces are read again so the router knows it, then
  // the journey continues at Agent without a page load. A failed read falls back to a page load.
  const openProject = async (p: ProjectCreated) => {
    const org = currentScope().org
    const href = `/org/${encodeURIComponent(org)}/project/${encodeURIComponent(p.id)}/setup`
    try {
      setWorkspaces((await fetchWorkspaces()).items)
    } catch {
      window.location.assign(href)
      return
    }
    await navigate({ href })
    setCurrent(null)
  }

  // Cloud only, and adding a project is an Admin's.
  if (install === "self-hosted") return <Navigate to="/overview" />
  if (!hasProject && (!org || roleIn(org.id, session) !== "Admin")) return <Navigate to="/overview" />
  const later = () => {
    setUpLater(currentScope().org, hasProject ? currentScope().project : null)
    void navigate({ to: "/overview" })
  }
  const step = current ?? null
  // The call stored: Call moves to First look at once.
  if (step === 3 && derived === 4) setCurrent(4)

  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <header className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-3 px-4 py-4 sm:grid-cols-[1fr_auto_1fr] sm:px-8">
        <span className="w-[120px] sm:w-[150px]">
          <BrandWordmark />
        </span>
        {step !== null && (
          <div className="col-span-2 row-start-2 justify-self-center sm:col-span-1 sm:col-start-2 sm:row-start-1">
            <Rail derived={derived} viewed={step} onView={go} />
          </div>
        )}
        <div className="flex justify-end sm:col-start-3 sm:row-start-1">
          {step !== null && step >= 1 && step <= 3 && (
            <Button variant="ghost" className="rounded-full text-[15px] text-muted-foreground" onClick={later}>
              Set up later
            </Button>
          )}
        </div>
      </header>
      <main className="mx-auto w-full max-w-[720px] flex-1 px-4 pt-10 pb-16 sm:pt-16">
        {step === null || install === "unknown" ? (
          <div className="flex justify-center pt-20">
            <Spinner className="size-6 text-muted-foreground" />
          </div>
        ) : step === 0 ? (
          <WelcomeScreen onStart={() => go(1)} />
        ) : step === 1 ? (
          hasProject ? (
            <ConnectedScreen livekit={read.data?.livekit} onContinue={() => go(Math.max(2, derived) as SetupStep)} />
          ) : (
            <ConnectScreen orgId={currentScope().org} onContinue={openProject} />
          )
        ) : step === 2 ? (
          read.data ? <AgentScreen connection={read.data} now={read.dataUpdatedAt} onContinue={toAgent} /> : <p className="text-[17px] text-muted-foreground">The connection could not be read.</p>
        ) : step === 3 ? (
          <CallScreen />
        ) : read.data?.firstCall ? (
          <FirstLookScreen callId={read.data.firstCall.id} />
        ) : (
          <p className="text-[17px] text-muted-foreground">No call is stored yet.</p>
        )}
      </main>
    </div>
  )
}
