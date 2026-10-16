import { useCallback } from "react"
import { useNavigate, useRouterState } from "@tanstack/react-router"
import { orgs } from "@/components/workspace-switcher"

// Every page lives under /org/<org>/project/<project>/, so a copied link opens the same project.
// Inside the app, routes stay short (/calls, /issues): the router's rewrite takes the scope off
// incoming URLs and puts it back on every URL it writes.

const scoped = /^\/org\/([^/]+)\/project\/([^/]+)(\/.*)?$/
const fallback = { org: orgs[0].id, project: orgs[0].projects[0].id }
let scope = { ...fallback }

export const currentScope = () => scope
export const scopePrefix = () => `/org/${scope.org}/project/${scope.project}`

export const scopeRewrite = {
  input: ({ url }: { url: URL }) => {
    const m = url.pathname.match(scoped)
    if (!m) return undefined
    const org = orgs.find((o) => o.id === m[1])
    const project = org?.projects.find((p) => p.id === m[2])
    // An unknown id falls back to the one project there is, rather than an empty console.
    scope = org && project ? { org: org.id, project: project.id } : { ...fallback }
    const next = new URL(url)
    next.pathname = m[3] ?? "/"
    return next
  },
  output: ({ url }: { url: URL }) => {
    if (scoped.test(url.pathname)) return undefined
    const next = new URL(url)
    next.pathname = scopePrefix() + (url.pathname === "/" ? "" : url.pathname)
    return next
  },
}

// One piece of page state kept in the URL's query, so the link shows exactly what is on screen.
// The default never appears in the URL; changes replace the history entry instead of adding one.
export function useUrlState<T>(key: string, initial: T): [T, (v: T) => void] {
  const raw = useRouterState({ select: (s) => (s.location.search as Record<string, unknown>)[key] })
  const navigate = useNavigate()
  const value = raw === undefined ? initial : (raw as T)
  const set = useCallback(
    (v: T) =>
      navigate({
        to: ".",
        replace: true,
        search: (prev: Record<string, unknown>) => ({ ...prev, [key]: JSON.stringify(v) === JSON.stringify(initial) ? undefined : v }),
      } as never),
    // `initial` is compared by value; a fresh literal each render must not rebuild the setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [navigate, key, JSON.stringify(initial)],
  )
  return [value, set]
}

// An open drawer kept in the URL by the id of what it shows (?room=RM_...), found again in `list`.
export function useUrlPick<T>(key: string, list: T[], id: (t: T) => string): [T | null, (t: T | null) => void] {
  const [v, set] = useUrlState<string | null>(key, null)
  return [v === null ? null : (list.find((t) => id(t) === v) ?? null), (t) => set(t ? id(t) : null)]
}

// True inside an embedded widget (decision 0023), where nothing may link into the console.
export const useEmbedded = () => useRouterState({ select: (s) => s.location.pathname.startsWith("/embed/") })
