// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { UsageAllowance } from "../src/components/usage-allowance"
import { ApiError } from "@/lib/api"
const state = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("@tanstack/react-query", () => ({ useQuery: () => state.value }))
vi.mock("@/lib/api", () => ({ entitlementQuery: () => ({}), ApiError: class extends Error { status: number; constructor(status: number) { super(); this.status = status } } }))
afterEach(cleanup)
describe("usage allowance", () => {
  it("hides an unavailable self-hosted entitlement endpoint", () => {
    state.value = { error: new ApiError(404, "not_found", "") }
    const { container } = render(<UsageAllowance />)
    expect(container.textContent).toBe("")
  })
  it("shows a quota warning without claiming calls will stop", () => {
    state.value = { data: { callSeconds: 114000, openCalls: 3, unmeasuredCalls: 2, limitCredits: 2000, retentionDays: null, maxProjects: null, maxMembers: null, from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z", warningPercent: 95, ingestAllowed: true } }
    render(<UsageAllowance />)
    expect(screen.getByText("95% of credits used")).toBeTruthy()
    expect(screen.getByText(/1,900 \/ 2,000 credits/)).toBeTruthy()
    expect(screen.queryByText(/up to/)).toBeNull()
    expect(screen.getByText(/3 open calls and 2 closed calls with unknown duration/)).toBeTruthy()
    expect(screen.getByText(/Oct 1, 2026.*until Nov 1, 2026.*UTC \(end exclusive\)/)).toBeTruthy()
    expect(screen.getByRole("link", { name: "Request more capacity" }).getAttribute("href")).toBe("https://voicegateway.dev/support")
  })
  it("says profiling stopped at zero credits, never the calls, and names the plan's limits", () => {
    state.value = { data: { callSeconds: 120000, openCalls: 0, unmeasuredCalls: 0, limitCredits: 2000, retentionDays: 30, maxProjects: 1, maxMembers: null, from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z", warningPercent: 100, ingestAllowed: false } }
    render(<UsageAllowance />)
    expect(screen.getByText("Profiling paused")).toBeTruthy()
    expect(screen.getByText(/New calls are not profiled until the plan renews or adds credits. Calls are never blocked./)).toBeTruthy()
    expect(screen.getByText("Call data kept 30 days, up to 1 project")).toBeTruthy()
  })
  it("does not turn a failed usage read into zero", () => {
    state.value = { isError: true }
    render(<UsageAllowance />)
    expect(screen.getByText("Usage unavailable")).toBeTruthy()
  })
})
