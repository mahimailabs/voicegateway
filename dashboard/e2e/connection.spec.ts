import { test, expect, type Page, type TestInfo } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

// The "Connect your agent" panel (first-call setup, acceptance check 8) at phone and desktop widths,
// from Settings > Connection. The preview's mock API receives no ingest, so by default every step
// waits and no first call exists; a test replaces top-level ProjectConnection fields through
// localStorage "vg.mock-connection".
const scope = "/org/default/project/default-project/"
const widths = [["phone", 390, 844], ["desktop", 1280, 800]] as const

async function open(page: Page, testInfo: TestInfo, width: number, height: number, override?: Record<string, unknown>) {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()) })
  await page.setViewportSize({ width, height })
  await page.addInitScript((theme) => localStorage.setItem("vg-theme", theme), testInfo.project.use.colorScheme ?? "light")
  if (override) await page.addInitScript((body) => localStorage.setItem("vg.mock-connection", body), JSON.stringify(override))
  await page.goto(scope + "overview?settings=connection")
  const panel = page.getByRole("region", { name: "Connect your agent" })
  await expect(panel).toBeVisible({ timeout: 15_000 })
  return { panel, errors }
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle")
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity).map((animation) => animation.finished.catch(() => undefined)))
  })
}

async function accessible(page: Page, testInfo: TestInfo, errors: string[]) {
  await settle(page)
  const results = await new AxeBuilder({ page }).analyze()
  await testInfo.attach("axe", { body: JSON.stringify(results, null, 2), contentType: "application/json" })
  expect.soft(errors, "browser console and uncaught errors").toEqual([])
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map((node) => node.target) })), "serious/critical axe violations").toEqual([])
}

const fitsWidth = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

for (const [label, width, height] of widths) {
  test(`connection panel at ${label} width is usable and accessible`, async ({ page }, testInfo) => {
    // Each width is set here, so the theme projects run it once each and the phone projects not at all.
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    const { panel, errors } = await open(page, testInfo, width, height)
    for (const name of ["Webhook", "Agent upload", "First call stored", "SDK (optional)"]) await expect(panel.getByRole("listitem", { name })).toContainText("Waiting")
    await expect(panel.getByRole("button", { name: "Copy agent upload url" })).toBeVisible()
    await expect(panel.getByRole("button", { name: "Copy webhook url" })).toBeVisible()
    await expect(panel.getByLabel("Webhook URL", { exact: true })).toHaveText(/\/ingest\/default\/default-project\/livekit\/webhook$/)
    // Nothing spills sideways at phone width.
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors)
  })

  test(`connection panel without public URLs at ${label} width is accessible`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    // A fresh self-hosted install: VOICEGATEWAY_PUBLIC_URL unset, so web builds no URL.
    const { panel, errors } = await open(page, testInfo, width, height, { urls: { observability: null, webhook: null } })
    await expect(panel.getByText(/set VOICEGATEWAY_PUBLIC_URL in deploy\/\.env/)).toHaveCount(1)
    await expect(panel.getByRole("button", { name: /^Copy/ })).toHaveCount(0)
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors)
  })

  test(`connection panel with sources in Error at ${label} width is accessible`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
    // The destructive Error badge, in both themes: a refusal with no accept, and a verified refusal
    // after an accept older than 10 minutes.
    const refused = new Date().toISOString()
    const { panel, errors } = await open(page, testInfo, width, height, {
      webhook: { lastAcceptedAt: new Date(Date.now() - 60 * 60_000).toISOString(), lastRejectedAt: refused, reason: "body_changed", usedKey: null, expectedKey: null },
      agent: { lastAcceptedAt: null, lastRejectedAt: refused, reason: "signature_invalid", usedKey: null, expectedKey: null },
    })
    for (const name of ["Webhook", "Agent upload"]) await expect(panel.getByRole("listitem", { name })).toContainText("Error")
    expect(await fitsWidth(page)).toBe(true)
    await accessible(page, testInfo, errors)
  })
}

test("a 64-character key in key_mismatch wraps at 390 px", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name.startsWith("phone-"), "the width is set by the test")
  const usedKey = "API" + "x".repeat(61)
  const { panel, errors } = await open(page, testInfo, 390, 844, {
    agent: { lastAcceptedAt: null, lastRejectedAt: new Date().toISOString(), reason: "key_mismatch", usedKey, expectedKey: "APIthisproject" },
  })
  const agent = panel.getByRole("listitem", { name: "Agent upload" })
  await expect(agent).toContainText("Error")
  await expect(agent.getByText(usedKey, { exact: true })).toBeVisible()
  expect(await fitsWidth(page)).toBe(true)
  await accessible(page, testInfo, errors)
})
