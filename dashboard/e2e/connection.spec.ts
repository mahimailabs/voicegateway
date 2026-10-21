import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

// The "Connect your agent" panel (first-call setup, acceptance check 8) at phone and desktop widths,
// from Settings > Connection. The preview's mock API receives no ingest, so every step waits and no
// first call exists.
const scope = "/org/default/project/default-project/"

for (const [label, width, height] of [["phone", 375, 812], ["desktop", 1280, 800]] as const) {
  test(`connection panel at ${label} width is usable and accessible`, async ({ page }, testInfo) => {
    // Each width is set here, so the theme projects run it once each and the phone projects not at all.
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()) })
    await page.setViewportSize({ width, height })
    await page.addInitScript((theme) => localStorage.setItem("vg-theme", theme), testInfo.project.use.colorScheme ?? "light")
    await page.goto(scope + "overview?settings=connection")
    const panel = page.getByRole("region", { name: "Connect your agent" })
    await expect(panel).toBeVisible({ timeout: 15_000 })
    for (const name of ["Webhook", "Agent upload", "First call stored"]) await expect(panel.getByRole("listitem", { name })).toContainText("Waiting")
    await expect(panel.getByRole("button", { name: "Copy observability url" })).toBeVisible()
    await expect(panel.getByRole("button", { name: "Copy webhook url" })).toBeVisible()
    await expect(panel.getByLabel("Webhook URL", { exact: true })).toHaveText(/\/ingest\/default\/default-project\/livekit\/webhook$/)
    // Nothing spills sideways at phone width.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.waitForLoadState("networkidle")
    await page.evaluate(() => document.fonts.ready)
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity).map((animation) => animation.finished.catch(() => undefined)))
    })
    const results = await new AxeBuilder({ page }).analyze()
    await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
    expect.soft(errors, "browser console and uncaught errors").toEqual([])
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
  })
}
