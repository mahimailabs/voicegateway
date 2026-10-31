import { test, expect, type Page, type TestInfo } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

// The capacity test dialog (docs/specs/capacity-runner.md) on the mock API: setup, a running test and
// a stopped one, each without serious or critical axe violations, in light and dark, phone and desktop.
async function axe(page: Page, testInfo: TestInfo, name: string) {
  // A button that just changed variant is still fading its colours, and the pointer may rest on
  // it after the click: check the settled frame with nothing hovered.
  await page.mouse.move(0, 0)
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter((animation) =>
      animation.effect?.getComputedTiming().iterations !== Infinity
    ).map((animation) => animation.finished.catch(() => undefined)))
  })
  const results = await new AxeBuilder({ page }).include("[role=dialog]").analyze()
  await testInfo.attach(`axe-${name}`, { body: JSON.stringify(results, null, 2), contentType: "application/json" })
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), `${name}: serious/critical axe violations`).toEqual([])
}

test("an Admin starts a capacity test, watches it and stops it", async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()) })
  await page.goto("/org/default/project/default-project/agents?capacityAgent=assistant-1ada")
  const dialog = page.getByRole("dialog", { name: "Test capacity" })
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  await axe(page, testInfo, "setup")

  const start = dialog.getByRole("button", { name: "Start test" })
  await expect(start).toBeDisabled()
  await dialog.getByRole("checkbox").check()
  await start.click()
  const stop = dialog.getByRole("button", { name: "Stop test" })
  await expect(stop).toBeVisible()
  await axe(page, testInfo, "running")

  await stop.click()
  await expect(dialog.getByText("stopped by a person").first()).toBeVisible()
  await axe(page, testInfo, "stopped")
  expect.soft(errors, "browser console and uncaught errors").toEqual([])
})
