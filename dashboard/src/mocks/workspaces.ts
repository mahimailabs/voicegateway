import type { WorkspaceProject, Workspaces } from "@/lib/api-types"
import { readLocal, writeLocal } from "@/lib/local-store"

// The organisations and projects the mock API answers for, until web reads them from Postgres. The
// open-source edition has one organisation; the server keeps the URL slugs unique.
const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63)
    .replace(/-$/, "")

const project = (name: string) => ({ id: slug(name), name })

// Projects added through the mock's createProject, kept in this browser so the page load that opens a
// new project still finds it. Only project metadata is kept; credentials are never stored.
type Added = { orgId: string; id: string; name: string }
const ADDED = "vg.mock-projects"
const added = (): Added[] => {
  const saved = readLocal(ADDED)
  return Array.isArray(saved) ? saved.filter((a): a is Added => typeof a?.orgId === "string" && typeof a?.id === "string" && typeof a?.name === "string") : []
}

// A browser check of the "No project yet" screen sets localStorage "vg.mock-empty-org", which adds a
// second organisation, "new-voice-team", with no project until one is added to it.
const EMPTY_ORG = "new-voice-team"
const emptyOrgWanted = () => readLocal("vg.mock-empty-org") === true
const projectsOf = (orgId: string) => added().filter((a) => a.orgId === orgId).map(({ id, name }) => ({ id, name }))

export const workspaces = (): Workspaces => ({
  items: [
    { id: "default", name: "Your organization", projects: [project("Default project"), ...projectsOf("default")] },
    ...(emptyOrgWanted() ? [{ id: EMPTY_ORG, name: "New voice team", projects: projectsOf(EMPTY_ORG) }] : []),
  ],
})

// A project the mock does not know is not found, as on the server.
export const knownProject = (orgId: unknown, projectId: unknown) => workspaces().items.some((o) => o.id === orgId && o.projects.some((p) => p.id === projectId))
export const knownOrg = (orgId: unknown) => workspaces().items.some((o) => o.id === orgId)

// createProject as web answers it: the id is the name's slug, a taken project id is refused.
export async function addProject(orgId: string, name: string, _livekitApiKey: string): Promise<WorkspaceProject | "project_exists"> {
  const p = project(name.trim())
  if (knownProject(orgId, p.id)) return "project_exists"
  writeLocal(ADDED, [...added(), { orgId, ...p }])
  return p
}
