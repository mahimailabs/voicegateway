import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

// What a VoiceGateway SDK attached to a call, in the preview's mock API: the one fixture call with SDK
// values shows its live state and custom fields in the drawer header, and a shared custom field filter
// reaches listCalls as field=key:value.
const scope = "/org/default/project/default-project/"
const sdkCall = "spike-room-11"

test("the call drawer shows the SDK's custom fields and an ended call's last state, accessibly", async ({ page }) => {
  await page.goto(`${scope}calls/${sdkCall}?range=%221-2000000000%22`)
  const dialog = page.getByRole("dialog")
  const fields = dialog.getByRole("list", { name: "Custom fields" })
  await expect(fields).toBeVisible({ timeout: 15_000 })
  await expect(fields.getByRole("listitem")).toHaveText(["customeracme", "plantrial"])
  // spike-room-11 has ended, so its state is the last one, with its time.
  const live = dialog.getByRole("list", { name: "Last state" })
  await expect(live.getByRole("listitem").first()).toContainText("Last state:")
  await expect(live.getByRole("listitem").nth(1)).toContainText("User listening")
  await expect(live.getByRole("listitem").last()).toContainText("Agent listening")
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const results = await new AxeBuilder({ page }).include('[data-slot="sheet-header"]').analyze()
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => ({ id: v.id, targets: v.nodes.map((n) => n.target) }))).toEqual([])
})

test("a call without an SDK shows no fields and no live state", async ({ page }) => {
  await page.goto(`${scope}calls/spike-room-10?range=%221-2000000000%22`)
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByRole("heading", { name: "spike-room-10" })).toBeVisible({ timeout: 15_000 })
  await expect(dialog.getByRole("list", { name: "Custom fields" })).toHaveCount(0)
  await expect(dialog.getByRole("list", { name: "Live state" })).toHaveCount(0)
  await expect(dialog.getByRole("list", { name: "Last state" })).toHaveCount(0)
})

test("a shared custom field filter is sent as field=key:value and keeps only matching calls", async ({ page }) => {
  const q = { id: "root", type: "group", combinator: "and", rules: [{ id: "field-customer", type: "rule", path: ["field"], operator: "is", value: "customer:acme" }] }
  const listed = page.waitForRequest((r) => new URL(r.url()).pathname.endsWith("/calls") && new URL(r.url()).searchParams.getAll("field").includes("customer:acme"))
  await page.goto(`${scope}calls?range=%221-2000000000%22&q=${encodeURIComponent(JSON.stringify(q))}`)
  await listed
  const links = page.locator("a[data-call-id]")
  await expect(links).toHaveCount(1, { timeout: 15_000 })
  await expect(links.first()).toHaveText(sdkCall)
})
