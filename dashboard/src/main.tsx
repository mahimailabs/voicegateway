import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "@tanstack/react-router"
import { QueryClientProvider } from "@tanstack/react-query"
import { IconContext } from "@phosphor-icons/react"
import { ThemeProvider } from "@/components/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { fetchWorkspaces } from "@/lib/api"
import { createQueryClient } from "@/lib/query-client"
import { setWorkspaces } from "@/lib/workspaces"
import { fetchSession, setCurrentSession, setMockApi } from "@/lib/session"
import { startEmbed } from "@/lib/embed"
import "./index.css"

// Captured data never changes while the page is open; refetching on focus would only flicker.
const queryClient = createQueryClient()

// The mock API answers in development until web serves the console's reads: VITE_MOCK_API=off turns
// it off, so every page shows what it shows when the API does not answer. A production build runs it
// only with VITE_MOCK_API=on. If it cannot start, the app still renders and each page says no API
// answered.
const mockApi = import.meta.env.VITE_MOCK_API === "on" || (import.meta.env.DEV && import.meta.env.VITE_MOCK_API !== "off")

async function start() {
  // An invitation or reset link opens before, and without, any session: the invitee has none yet.
  if (window.location.pathname === "/invite") {
    const { InvitePage, readInviteToken } = await import("@/features/auth/invite-page")
    const token = readInviteToken()
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <ThemeProvider>
          <InvitePage token={token} onAccepted={() => window.location.assign("/")} />
        </ThemeProvider>
      </StrictMode>,
    )
    return
  }
  setMockApi(mockApi)
  if (mockApi) {
    try {
      const { startMockApi } = await import("@/mocks/browser")
      await startMockApi()
    } catch (e) {
      console.error("The mock API did not start; pages will show that no API answered.", e)
    }
  }
  // A widget framed on another site reads with its embed token alone: no session, no workspaces.
  const embedded = startEmbed() !== null
  // With the server, nobody reads anything before signing in (decision 0027). If no API answers,
  // the pages say so, as before.
  if (!mockApi && !embedded) {
    const read = await fetchSession().catch(() => null)
    if (read && !read.signedIn) {
      const { SignInScreen } = await import("@/features/auth/sign-in-screen")
      createRoot(document.getElementById("root")!).render(
        <StrictMode>
          <ThemeProvider>
            <SignInScreen needsAdmin={read.needsAdmin} onSignedIn={() => window.location.reload()} />
          </ThemeProvider>
        </StrictMode>,
      )
      return
    }
    if (read?.signedIn) setCurrentSession(read.session)
  }
  // The router decides which project a URL names, so it loads once the workspaces are read.
  setWorkspaces(embedded ? null : await fetchWorkspaces().then((w) => w.items, () => null))
  const { router } = await import("@/router")
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <IconContext.Provider value={{ size: 18, weight: "regular" }}>
            <TooltipProvider delayDuration={200}>
              <RouterProvider router={router} />
            </TooltipProvider>
          </IconContext.Provider>
        </ThemeProvider>
      </QueryClientProvider>
    </StrictMode>,
  )
}
void start()
