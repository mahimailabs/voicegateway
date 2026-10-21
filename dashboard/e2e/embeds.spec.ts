import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
import capture from "../src/mocks/capture-session.json" with { type: "json" }
import { sizes, widgets } from "../src/features/embed/widgets.js"

// Every widget at every size it comes in, in its own frame size, as a platform shows it (decision
// 0034). The preview reads with the mock session; the token path is covered by the Go tests.
const scope = "/org/default/project/default-project/embed/"

for (const w of widgets) {
  for (const size of w.sizes) {
    test(`${w.id} ${size} widget accessibility`, async ({ page }, testInfo) => {
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))
      page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()) })
      await page.setViewportSize(sizes[size])
      const theme = testInfo.project.use.colorScheme === "dark" ? "dark" : "light"
      const path = `${scope}${w.id}?theme=${theme}${w.sizes.length > 1 ? `&size=${size}` : ""}${w.id === "call" ? `&call=${encodeURIComponent(capture.call.room)}` : ""}`
      const response = await page.goto(path)
      expect(response?.status()).toBe(200)
      if (w.id === "call") await expect(page.getByText(/turns? in /).first()).toBeVisible({ timeout: 15_000 })
      else await expect(page.getByText("Powered by VoiceGateway").or(page.getByLabel("Powered by VoiceGateway")).first()).toBeVisible({ timeout: 15_000 })
      await page.waitForLoadState("networkidle")
      await page.evaluate(() => document.fonts.ready)
      await page.evaluate(async () => {
        await Promise.all(document.getAnimations().filter((animation) =>
          animation.effect?.getComputedTiming().iterations !== Infinity
        ).map((animation) => animation.finished.catch(() => undefined)))
      })
      const results = await new AxeBuilder({ page }).analyze()
      await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
      expect.soft(errors, "browser console and uncaught errors").toEqual([])
      expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
    })
  }
}
