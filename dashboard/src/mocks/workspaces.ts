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
// new project still finds it. Only project metadata and a key fingerprint are kept; credentials are never stored.
type Added = { orgId: string; id: string; name: string; keyFingerprint: string }
const ADDED = "vg.mock-projects"
const added = (): Added[] => {
  const saved = readLocal(ADDED)
  return Array.isArray(saved) ? saved.filter((a): a is Added => typeof a?.orgId === "string" && typeof a?.id === "string" && typeof a?.name === "string" && typeof a?.keyFingerprint === "string") : []
}

export const workspaces = (): Workspaces => ({
  items: [{ id: "default", name: "Your organization", projects: [project("Default project"), ...added().filter((a) => a.orgId === "default").map(({ id, name }) => ({ id, name }))] }],
})

// A project the mock does not know is not found, as on the server.
export const knownProject = (orgId: unknown, projectId: unknown) => workspaces().items.some((o) => o.id === orgId && o.projects.some((p) => p.id === projectId))
export const knownOrg = (orgId: unknown) => workspaces().items.some((o) => o.id === orgId)

// createProject as web answers it: the id is the name's slug, a taken id or LiveKit key is refused.
export async function addProject(orgId: string, name: string, livekitApiKey: string): Promise<WorkspaceProject | "project_exists" | "livekit_key_in_use"> {
  // Web Crypto avoids persisting a credential just to reproduce the API's uniqueness check.
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(livekitApiKey))
  const keyFingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")
  const p = project(name.trim())
  if (knownProject(orgId, p.id)) return "project_exists"
  if (added().some((a) => a.keyFingerprint === keyFingerprint)) return "livekit_key_in_use"
  writeLocal(ADDED, [...added(), { orgId, ...p, keyFingerprint }])
  return p
}
