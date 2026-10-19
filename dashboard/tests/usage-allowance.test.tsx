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
    state.value = { data: { callSeconds: 114000, limitMinutes: 2000, from: "2026-10-01T00:00:00Z", to: "2026-11-01T00:00:00Z", warningPercent: 95, ingestAllowed: true } }
    render(<UsageAllowance />)
    expect(screen.getByText("95% of allowance used")).toBeTruthy()
    expect(screen.getByText(/Oct 1, 2026.*until Nov 1, 2026.*UTC \(end exclusive\)/)).toBeTruthy()
    expect(screen.getByRole("link", { name: "Request more capacity" }).getAttribute("href")).toBe("https://voicegateway.dev/support")
  })
  it("does not turn a failed usage read into zero", () => {
    state.value = { isError: true }
    render(<UsageAllowance />)
    expect(screen.getByText("Usage unavailable")).toBeTruthy()
  })
})
