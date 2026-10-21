import { test, expect } from "@playwright/test"
import capture from "../src/mocks/capture-session.json" with { type: "json" }

const scope = "/org/default/project/default-project/"
const cases = [
  { path: "calls", first: /^Calls /, next: /^Archived / },
  { path: "issues", first: /^Open /, next: /^Resolved / },
  { path: "calls/" + encodeURIComponent(capture.call.room), first: "Insights", next: "Call path" },
] as const

for (const { path, first, next } of cases) {
  test(path + " tabs support keyboard navigation and labelled panels", async ({ page }, testInfo) => {
    await page.addInitScript((theme) => localStorage.setItem("vg-theme", theme), testInfo.project.use.colorScheme ?? "light")
    await page.goto(scope + path)
    const initial = page.getByRole("tab", { name: first })
    const following = page.getByRole("tab", { name: next })
    // The page is ready once the tabs under test render: on a call that is after getCall answers and
    // the drawer draws, which a loaded CI runner has taken more than the 5 s assertion budget to do
    // (the same 15 s first-render wait as the other e2e specs). The keyboard checks keep their budget.
    await initial.waitFor({ timeout: 15_000 })
    await expect(initial).toHaveAttribute("aria-selected", "true")
    await initial.focus()
    await page.keyboard.press("ArrowRight")
    await expect(following).toBeFocused()
    await expect(following).toHaveAttribute("aria-selected", "true")
    const panel = page.getByRole("tabpanel", { name: next })
    await expect(panel).toBeVisible()
    await expect(panel).toHaveAttribute("id", (await following.getAttribute("aria-controls"))!)
    await page.keyboard.press("ArrowLeft")
    await expect(initial).toBeFocused()
    await expect(page.getByRole("tabpanel", { name: first })).toBeVisible()
  })
}
