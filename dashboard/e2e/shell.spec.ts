import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"
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
] as const

for (const [path, heading] of pages) {
  test(path + " deep link and accessibility", async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on("pageerror", (error) => errors.push(error.message))
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()) })
    await page.addInitScript((theme) => localStorage.setItem("vg-theme", theme), testInfo.project.name)
    const response = await page.goto(scope + path)
    expect(response?.status()).toBe(200)
    await expect(page).toHaveURL(new RegExp(scope + path + "(?:\\?|$)"))
    await expect(page.locator("html")).toHaveClass(testInfo.project.name === "dark" ? /dark/ : /^(?!.*\bdark\b)/)
    if (heading) await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible()
    else await expect(page.getByRole("textbox", { name: "Filter rooms", exact: true })).toBeVisible()
    await expect(page.getByText("Loading calls", { exact: true })).toHaveCount(0)
    if (path === "overview") {
      const links = await page.locator('[data-sidebar="menu-button"][href^="/"]').evaluateAll((items) => items.map((item) => item.getAttribute("href")))
      expect(links.length).toBeGreaterThan(0)
      for (const href of links) expect(pages.map(([route]) => scope + route), "every internal sidebar link has a test").toContain(href)
    }
    await page.waitForLoadState("networkidle")
    // Wait for the built page's fonts before contrast analysis.
    await page.evaluate(() => document.fonts.ready)
    const results = await new AxeBuilder({ page }).analyze()
    await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
    await testInfo.attach("browser-errors", { body: JSON.stringify(errors), contentType: "application/json" })
    expect.soft(errors, "browser console and uncaught errors").toEqual([])
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
  })
}
