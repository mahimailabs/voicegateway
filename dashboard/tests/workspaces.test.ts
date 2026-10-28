import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { addProject, knownProject } from "../src/mocks/workspaces.js"

let stored: Map<string, string>
beforeEach(() => {
  stored = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  })
})
afterEach(() => vi.unstubAllGlobals())

it("persists project discovery and duplicate detection without the raw LiveKit key", async () => {
  const key = "test-livekit-key-never-store"
  expect(await addProject("default", "Production", key)).toEqual({ id: "production", name: "Production" })
  expect(knownProject("default", "production")).toBe(true)
  const saved = stored.get("vg.mock-projects")!
  expect(saved).not.toContain(key)
  expect(JSON.parse(saved)[0]).not.toHaveProperty("livekitApiKey")

  // Reload the module so uniqueness must come from persisted data, not module memory.
  vi.resetModules()
  const reloaded = await import("../src/mocks/workspaces.js")
  expect(reloaded.knownProject("default", "production")).toBe(true)
  expect(await reloaded.addProject("default", "Other", key)).toEqual({ id: "other", name: "Other" })
  expect(await reloaded.addProject("default", "Production", "another-test-key")).toBe("project_exists")
  expect(await reloaded.addProject("default", "Staging", "another-test-key")).toEqual({ id: "staging", name: "Staging" })
  expect(stored.get("vg.mock-projects")).not.toContain("another-test-key")
})

it("allows independent projects with the same key", async () => {
  const results = await Promise.all([addProject("default", "One", "shared-test-key"), addProject("default", "Two", "shared-test-key")])
  expect(results).toEqual([{ id: "one", name: "One" }, { id: "two", name: "Two" }])
  expect(JSON.parse(stored.get("vg.mock-projects")!)).toHaveLength(2)
})
