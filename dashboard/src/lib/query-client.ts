import { QueryCache, QueryClient } from "@tanstack/react-query"
import { accessDenied, shouldRetry } from "@/lib/api"

export function createQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (accessDenied(error) && ["media-server", "agents", "workers", "resources"].includes(String(query.queryKey[0]))) {
          // Notify every mounted observer, including consumers that read only data.
          // Keep the denial and polling lifecycle so restored access can recover.
          query.setState({ data: undefined, dataUpdatedAt: 0 })
        }
      },
    }),
    defaultOptions: { queries: { staleTime: Infinity, refetchOnWindowFocus: false, retry: shouldRetry } },
  })
}
