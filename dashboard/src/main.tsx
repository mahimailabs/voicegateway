import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "@tanstack/react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { IconContext } from "@phosphor-icons/react"
import { ThemeProvider } from "@/components/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { router } from "@/router"
import "./index.css"

// Captured data never changes while the page is open; refetching on focus would only flicker.
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, refetchOnWindowFocus: false, retry: 1 } } })

// The mock API answers until web serves the console's reads. VITE_MOCK_API=off turns it off, so
// every page shows what it shows when the API does not answer.
async function start() {
  if (import.meta.env.VITE_MOCK_API !== "off") {
    const { startMockApi } = await import("@/mocks/browser")
    await startMockApi()
  }
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
