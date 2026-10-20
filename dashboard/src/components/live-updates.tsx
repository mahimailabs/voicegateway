import { createContext, useContext, useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { currentScope } from "@/lib/url-state"
import { isMockApi } from "@/lib/session"
import { watchLiveUpdates, type LiveConnection } from "@/lib/live-updates"

const Connection = createContext<LiveConnection | null>(null)
export const useLiveConnection = () => useContext(Connection)

export function LiveUpdatesProvider({ children }: { children: React.ReactNode }) {
  const client = useQueryClient()
  const { org, project } = currentScope()
  const [connection, setConnection] = useState<LiveConnection | null>(null)
  useEffect(() => {
    if (isMockApi()) return
    let controller: AbortController | undefined
    const start = () => {
      controller?.abort()
      if (document.hidden) { setConnection("paused"); return }
      controller = new AbortController()
      const active = controller
      void watchLiveUpdates({ org, project }, client, setConnection, active.signal)
        .catch(() => { if (!active.signal.aborted) setConnection("disconnected") })
    }
    start()
    document.addEventListener("visibilitychange", start)
    return () => { controller?.abort(); document.removeEventListener("visibilitychange", start) }
  }, [org, project, client])
  return <Connection.Provider value={connection}>{children}</Connection.Provider>
}
