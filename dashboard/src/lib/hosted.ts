import { useQuery } from "@tanstack/react-query"
import { ApiError, entitlementQuery } from "@/lib/api"

export type Install = "hosted" | "self-hosted" | "unknown"

// Which install the console talks to. The entitlement API exists only on a hosted install: web
// answers its 404 on a self-hosted one (internal/api optionalEntitlement), and an answer means hosted.
// Anything else, including still loading or a failed read, is unknown, never guessed.
export function installOf(read: { data?: unknown; error?: unknown }): Install {
  if (read.error instanceof ApiError && read.error.status === 404) return "self-hosted"
  if (read.data) return "hosted"
  return "unknown"
}

/** The install kind, from the same entitlement read the usage allowance shows. */
export const useHosted = (): Install => installOf(useQuery(entitlementQuery()))
