import type { Workspace } from "@/lib/api-types"

// The organisations and projects this person may read: getWorkspaces, read once when the console
// starts (main.tsx), before the router decides which project a URL names. null when it could not be
// read; an empty list is an observed answer, never a reason to invent a workspace.
let list: Workspace[] | null = null
export const setWorkspaces = (w: Workspace[] | null) => {
  list = w
}
export const workspaces = (): Workspace[] => list ?? []
export const workspacesRead = () => list !== null
