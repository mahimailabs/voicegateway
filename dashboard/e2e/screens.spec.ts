import { expect, test } from "@playwright/test"
import { pages, scope } from "./pages.ts"
import { slug } from "./screens-catalog.ts"

/**
 * The screen catalog: one picture of every console page, drawn from the real code on the mock API's
 * captured sample data, so anyone (person or coding agent) can see what exists before proposing UI.
 * Run with `make dashboard-screens`; pictures land in docs/screens/, and screens-catalog.ts writes screens.json.
 */
const out = new URL("../../docs/screens/", import.meta.url)

for (const p of pages) {
  test(p.title, async ({ page }, testInfo) => {
    const theme = testInfo.project.use.colorScheme === "dark" ? "dark" : "light"
    await page.addInitScript((t) => localStorage.setItem("vg-theme", t), theme)
    await page.goto(scope + p.path)
    if (p.heading) await expect(page.getByRole("heading", { name: p.heading, exact: true })).toBeVisible({ timeout: 15_000 })
    else await expect(page.getByRole("textbox", { name: "Filter rooms", exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText("Loading calls", { exact: true })).toHaveCount(0)
    await page.waitForLoadState("networkidle")
    await page.evaluate(() => document.fonts.ready)
    // Let entry fades finish, so a dialog or drawer is pictured settled, not mid-fade.
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => undefined)))
    })
    // A dialog or drawer covers only the viewport, so a full-page picture would cut it off half way.
    const overlay = (await page.getByRole("dialog").count()) > 0
    const name = `${slug(p.title)}.${testInfo.project.name}.jpg`
    await page.screenshot({ path: new URL(name, out).pathname, fullPage: !overlay, type: "jpeg", quality: 75, animations: "disabled" })
  })
}
