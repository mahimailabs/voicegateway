// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, expect, test } from "vitest"
import { CallFields, CallLive } from "../src/features/call/call-sdk"
import { relative } from "../src/lib/relative-time"
import { calls, showCalls } from "./calls-ui"

// What an SDK attached to a call: custom fields and the latest user and agent state. Unknown stays
// unknown: a call no SDK reported on shows neither, never a placeholder.

afterEach(cleanup)
const now = Date.parse("2026-10-21T02:26:55.810Z")

test("custom fields show each key and value, sorted by key", () => {
  render(<CallFields call={{ fields: { plan: "trial", customer: "acme" } }} />)
  const items = within(screen.getByRole("list", { name: "Custom fields" })).getAllByRole("listitem")
  expect(items.map((li) => li.textContent)).toEqual(["customeracme", "plantrial"])
  expect(items[0].querySelector("[title]")?.getAttribute("title")).toBe("customer: acme")
})

test.each([{}, { fields: {} }])("no custom fields render nothing (%o)", (call) => {
  const { container } = render(<CallFields call={call} />)
  expect(container.innerHTML).toBe("")
})

test("live state shows user and agent state with a relative time", () => {
  render(<CallLive now={now} call={{ live: { user: { state: "speaking", at: "2026-10-21T02:26:52.810Z" }, agent: { state: "thinking", at: "2026-10-21T02:21:55.810Z" } } }} />)
  const items = within(screen.getByRole("list", { name: "Live state" })).getAllByRole("listitem")
  expect(items.map((li) => li.textContent)).toEqual([`User speaking · ${relative("2026-10-21T02:26:52.810Z", now)}`, `Agent thinking · ${relative("2026-10-21T02:21:55.810Z", now)}`])
  expect(items[1].querySelector("time")?.getAttribute("dateTime")).toBe("2026-10-21T02:21:55.810Z")
})

test("only the actor the SDK reported is shown", () => {
  render(<CallLive now={now} call={{ live: { user: null, agent: { state: "listening", at: "2026-10-21T02:26:55.810Z" } } }} />)
  expect(within(screen.getByRole("list", { name: "Live state" })).getAllByRole("listitem").map((li) => li.textContent?.startsWith("Agent listening"))).toEqual([true])
  expect(screen.queryByText(/^User/)).toBeNull()
})

test.each([{ live: null }, {}, { live: { user: null, agent: null } }])("no live state renders nothing (%o)", (call) => {
  const { container } = render(<CallLive now={now} call={call} />)
  expect(container.innerHTML).toBe("")
})

test("relative time uses the largest whole unit", () => {
  expect(relative("2026-10-21T02:26:52.810Z", now)).toBe(new Intl.RelativeTimeFormat([], { numeric: "auto" }).format(-3, "second"))
  expect(relative("2026-10-21T02:21:55.810Z", now)).toBe(new Intl.RelativeTimeFormat([], { numeric: "auto" }).format(-5, "minute"))
  expect(relative("2026-10-18T02:26:55.810Z", now)).toBe(new Intl.RelativeTimeFormat([], { numeric: "auto" }).format(-3, "day"))
})

test("the call drawer shows the SDK's fields and live state from getCall", async () => {
  const call = calls.find((c) => c.live)!
  await showCalls(call.id)
  const dialog = within(await screen.findByRole("dialog"))
  const fields = await dialog.findByRole("list", { name: "Custom fields" })
  expect(within(fields).getAllByRole("listitem").map((li) => li.textContent)).toEqual(Object.entries(call.fields!).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => k + v))
  const live = dialog.getByRole("list", { name: "Live state" })
  expect(within(live).getByText(`User ${call.live!.user!.state}`, { exact: false })).toBeTruthy()
  expect(within(live).getByText(`Agent ${call.live!.agent!.state}`, { exact: false })).toBeTruthy()
})

test("a call no SDK reported on shows no fields and no live state", async () => {
  const call = calls.find((c) => !c.live && !Object.keys(c.fields ?? {}).length)!
  await showCalls(call.id)
  const dialog = within(await screen.findByRole("dialog"))
  expect(await dialog.findByText(call.room, { selector: "h2" })).toBeTruthy()
  expect(dialog.queryByRole("list", { name: "Custom fields" })).toBeNull()
  expect(dialog.queryByRole("list", { name: "Live state" })).toBeNull()
})
