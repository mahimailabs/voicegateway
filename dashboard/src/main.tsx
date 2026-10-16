import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "@tanstack/react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { IconContext } from "@phosphor-icons/react"
import { ThemeProvider } from "@/components/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { router } from "@/router"
import { shouldRetry } from "@/lib/api"
import "./index.css"

// Captured data never changes while the page is open; refetching on focus would only flicker.
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, refetchOnWindowFocus: false, retry: shouldRetry } } })

// The mock API answers in development until web serves the console's reads: VITE_MOCK_API=off turns
// it off, so every page shows what it shows when the API does not answer. A production build runs it
// only with VITE_MOCK_API=on. If it cannot start, the app still renders and each page says no API
// answered.
const mockApi = import.meta.env.VITE_MOCK_API === "on" || (import.meta.env.DEV && import.meta.env.VITE_MOCK_API !== "off")

async function start() {
  if (mockApi) {
    try {
      const { startMockApi } = await import("@/mocks/browser")
      await startMockApi()
    } catch (e) {
      console.error("The mock API did not start; pages will show that no API answered.", e)
    }
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
