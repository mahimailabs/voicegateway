import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
import { watchErrors } from "./console-errors.ts"
import capture from "../src/mocks/capture-session.json" with { type: "json" }

const scope = "/org/default/project/default-project/"
const pages = [
  ["overview", "Overview"],
  ["calls", "Calls"],
  ["issues", /^\d+ open/],
  ["alerts", "Alerts"],
  ["agents", "Agent workers"],
  ["sfu", "Media server"],
  ["sip", "Phone bridge"],
  ["egress", "Egress"],
  ["ingress", "Ingress"],
  ["project", /Everything this project/],
  ["live", null],
  ["settings", "Settings"],
  ["docs", "VoiceGateway API"],
  ["calls/" + encodeURIComponent(capture.call.room), capture.call.room],
  ["calls/" + encodeURIComponent(capture.call.room) + "?callView=diagnosis", capture.call.room],
] as const

for (const [path, heading] of pages) {
  test(path + " deep link and accessibility", async ({ page }, testInfo) => {
    const { errors, unexpected } = watchErrors(page)
    await page.addInitScript((theme) => localStorage.setItem("vg-theme", theme), testInfo.project.use.colorScheme ?? "light")
    const response = await page.goto(scope + path)
    expect(response?.status()).toBe(200)
    await expect(page).toHaveURL((url) => url.pathname + url.search === scope + path)
    await expect(page.locator("html")).toHaveClass(testInfo.project.use.colorScheme === "dark" ? /dark/ : /^(?!.*\bdark\b)/, { timeout: 15_000 })
    // Allow the mock worker and lazy route to finish cold startup on a busy host.
    if (heading) await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible({ timeout: 15_000 })
    else await expect(page.getByRole("textbox", { name: "Filter rooms", exact: true })).toBeVisible()
    await expect(page.getByText("Loading calls", { exact: true })).toHaveCount(0)
    // The desktop sidebar provides the complete route inventory; phones collapse it.
    if (path === "overview" && (page.viewportSize()?.width ?? 0) >= 768) {
      const links = await page.locator('[data-sidebar="menu-button"][href^="/"]').evaluateAll((items) => items.map((item) => item.getAttribute("href")))
      expect(links.length).toBeGreaterThan(0)
      for (const href of links) expect(pages.map(([route]) => scope + route), "every internal sidebar link has a test").toContain(href)
    }
    await page.waitForLoadState("networkidle")
    // Wait for the built page's fonts before contrast analysis.
    await page.evaluate(() => document.fonts.ready)
    // Scan the settled drawer, not an intermediate fade with blended text colors.
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter((animation) =>
        animation.effect?.getComputedTiming().iterations !== Infinity
      ).map((animation) => animation.finished.catch(() => undefined)))
    })
    const results = await new AxeBuilder({ page }).analyze()
    await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
    await testInfo.attach("browser-errors", { body: JSON.stringify(errors), contentType: "application/json" })
    expect.soft(unexpected(), "browser console and uncaught errors").toEqual([])
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
  })
}
