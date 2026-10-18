import { describe, expect, it } from "vitest"
import { currentScope, entryPath, firstProjectPath, orgOnly, scopeRewrite, scopeUnknown } from "../src/lib/url-state.ts"
import { setWorkspaces } from "../src/lib/workspaces.ts"
import type { Workspace } from "../src/lib/api-types/index.ts"

const read = [{ id: "acme", name: "Acme", projects: [{ id: "prod", name: "Production" }] }] as unknown as Workspace[]
const open = (path: string) => scopeRewrite.input({ url: new URL(`http://console${path}`) })

// A link to a project this person cannot read must say so, never open another project.
describe("unknown scope", () => {
  it.each([
    ["unknown project", "/org/acme/project/x/issues", true],
    ["unknown organisation", "/org/nope/project/prod/issues", true],
    ["known project", "/org/acme/project/prod/issues", false],
  ])("%s", (_, path, unknown) => {
    setWorkspaces(read)
    expect(open(path)?.pathname).toBe("/issues")
    expect(scopeUnknown()).toBe(unknown)
    const [, , org, , project] = path.split("/")
    expect(currentScope()).toEqual({ org, project })
    expect(entryPath({ pathname: path, search: "", hash: "" })).toBeNull()
  })

  it("links the state to the first readable project", () => {
    setWorkspaces(read)
    expect(firstProjectPath()).toBe("/org/acme/project/prod/overview")
    setWorkspaces([])
    expect(firstProjectPath()).toBeNull()
  })

  it("keeps the URL without the state when the workspaces could not be read", () => {
    setWorkspaces(null)
    open("/org/acme/project/x/issues")
    expect(scopeUnknown()).toBe(false)
    expect(currentScope()).toEqual({ org: "acme", project: "x" })
  })

  it("moves only a bare link under the current project", () => {
    setWorkspaces(read)
    open("/org/acme/project/prod/")
    expect(entryPath({ pathname: "/calls", search: "?q=1", hash: "" })).toBe("/org/acme/project/prod/calls?q=1")
  })

  // A 06b review follow-up: an organisation with no project yet still opens its organisation
  // settings, so its admin can manage People before adding a project.
  it("keeps the organisation of a member whose organisation has no project", () => {
    setWorkspaces([{ id: "acme", name: "Acme", projects: [] }] as unknown as Workspace[])
    open("/org/acme/project/default-project/")
    expect(scopeUnknown()).toBe(true)
    expect(orgOnly()).toBe(true)
    open("/org/nope/project/default-project/")
    expect(orgOnly()).toBe(false)
  })
})
