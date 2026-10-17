import type { Workspaces } from "@/lib/api-types"

// The organisations and projects the mock API answers for, until web reads them from Postgres. The
// open-source edition has one organisation; the server keeps the URL slugs unique.
const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")

const project = (name: string) => ({ id: slug(name), name })

export const workspaces = (): Workspaces => ({ items: [{ id: "default", name: "Your organization", projects: [project("Default project")] }] })

// A project the mock does not know is not found, as on the server.
export const knownProject = (orgId: unknown, projectId: unknown) => workspaces().items.some((o) => o.id === orgId && o.projects.some((p) => p.id === projectId))
export const knownOrg = (orgId: unknown) => workspaces().items.some((o) => o.id === orgId)
