import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { CaptureResult } from "posthog-js"

const posthog = vi.hoisted(() => ({ init: vi.fn(), identify: vi.fn(), register: vi.fn(), capture: vi.fn(), reset: vi.fn() }))
vi.mock("posthog-js", () => ({ default: posthog }))

import { identify, scrub, startAnalytics, stopAnalyticsForTest, track } from "@/lib/analytics"
import { fetchClientConfig } from "@/lib/client-config"

beforeEach(() => {
  stopAnalyticsForTest()
  for (const f of Object.values(posthog)) f.mockReset()
})

afterEach(() => vi.unstubAllGlobals())

// Decision 0038: a self-hosted install that set no key sends nothing at all.
describe("off", () => {
  it("does not start PostHog without a key, and records nothing", async () => {
    await startAnalytics(null)
    track({ name: "project_created", organisation_id: "org-a" })
    identify({ id: "u1", email: "ada@example.com", organisations: [] })
    expect(posthog.init).not.toHaveBeenCalled()
    expect(posthog.capture).not.toHaveBeenCalled()
    expect(posthog.identify).not.toHaveBeenCalled()
  })

  it("reads a failed or malformed client config as off, and a project key as on", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")))
    expect((await fetchClientConfig()).posthog).toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })))
    expect((await fetchClientConfig()).posthog).toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ posthog: { key: "", host: "https://x" } })))
    expect((await fetchClientConfig()).posthog).toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ sentry: null, posthog: { key: "phc_k", host: "https://us.i.posthog.com" } })))
    expect((await fetchClientConfig()).posthog).toEqual({ key: "phc_k", host: "https://us.i.posthog.com" })
  })
})

describe("on", () => {
  const on = { key: "phc_test", host: "https://us.i.posthog.com" }

  it("records page views and named events only: no autocapture, replay, exceptions or flags", async () => {
    await startAnalytics(on)
    expect(posthog.init).toHaveBeenCalledOnce()
    const [key, config] = posthog.init.mock.calls[0]
    expect(key).toBe("phc_test")
    expect(config).toMatchObject({
      api_host: "https://us.i.posthog.com",
      autocapture: false,
      capture_pageview: "history_change",
      disable_session_recording: true,
      capture_exceptions: false,
      capture_heatmaps: false,
      advanced_disable_flags: true,
      disable_external_dependency_loading: true,
      person_profiles: "identified_only",
    })
  })

  it("names the user by id, never by email", async () => {
    await startAnalytics(on)
    identify({ id: "u1", email: "ada@example.com", organisations: [{ id: "org-a", role: "admin" }] })
    expect(posthog.identify).toHaveBeenCalledWith("u1")
    expect(JSON.stringify([...posthog.identify.mock.calls, ...posthog.register.mock.calls])).not.toContain("ada@example.com")
  })

  it("sends an event's own properties", async () => {
    await startAnalytics(on)
    track({ name: "setup_step_reached", organisation_id: "org-a", step: 2 })
    expect(posthog.capture).toHaveBeenCalledWith("setup_step_reached", { organisation_id: "org-a", step: 2 })
  })
})

// A calls filter or search in the URL can hold what a caller said, so only origin and path leave.
describe("scrub", () => {
  it("drops the query and fragment of every URL property", () => {
    const event = {
      uuid: "e",
      event: "$pageview",
      properties: { $current_url: "https://vg.example/calls?q=my+card+number#x", $referrer: "https://vg.example/org/a?search=hi", other: "kept?yes" },
      $set_once: { $initial_current_url: "https://vg.example/setup?step=2" },
    } as unknown as CaptureResult
    const out = scrub(event)!
    expect(out.properties.$current_url).toBe("https://vg.example/calls")
    expect(out.properties.$referrer).toBe("https://vg.example/org/a")
    expect(out.properties.other).toBe("kept?yes")
    expect(out.$set_once?.$initial_current_url).toBe("https://vg.example/setup")
  })
})
